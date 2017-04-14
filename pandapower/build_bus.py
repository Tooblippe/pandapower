# -*- coding: utf-8 -*-

# Copyright (c) 2016-2017 by University of Kassel and Fraunhofer Institute for Wind Energy and
# Energy System Technology (IWES), Kassel. All rights reserved. Use of this source code is governed
# by a BSD-style license that can be found in the LICENSE file.

from collections import defaultdict
from itertools import chain

import numpy as np
import pandas as pd
from pypower.idx_bus import BUS_I, BASE_KV, PD, QD, GS, BS, VMAX, VMIN, BUS_TYPE, NONE, VM, VA
# column ppc['bus'] indices for constant current loads real and imaginary part...
# TODO remove PCID and QCID from here this after creating unique ppc indexing file
PCID = 13   # active power corresponding to constant current at rated voltage
QCID = 14   # reactive power corresponding to constant current at rated voltage
from pandapower.auxiliary import _sum_by_group


try:
    from numba import jit
except:
    pass


@jit(nopython=True, cache=True)
def ds_find(ar, bus):
    while True:
        p = ar[bus]
        if p == bus:
            break
        bus = p
    return p


@jit(nopython=True, cache=True)
def ds_union(ar, bus1, bus2, bus_is_pv):
    root1 = ds_find(ar, bus1)
    root2 = ds_find(ar, bus2)
    if root1 == root2:
        return
    if bus_is_pv[root2]:
        if bus_is_pv[root1]:
            raise UserWarning("Can't fuse two PV buses")
        ar[root1] = root2
    else:
        ar[root2] = root1


@jit(nopython=True, cache=True)
def ds_create(ar, switch_bus, switch_elm, switch_et_bus, switch_closed, bus_is_pv, bus_in_service):
    for i in range(len(switch_bus)):
        if not switch_closed[i] or not switch_et_bus[i]:
            continue
        bus1 = switch_bus[i]
        bus2 = switch_elm[i]
        if bus_in_service[bus1] and bus_in_service[bus2]:
            ds_union(ar, bus1, bus2, bus_is_pv)


@jit(nopython=True, cache=True)
def fill_bus_lookup(ar, bus_lookup, bus_index):
    for i in range(len(bus_index)):
        bus_lookup[bus_index[i]] = i
    for b in bus_index:
        ds = ds_find(ar, b)
        bus_lookup[b] = bus_lookup[ar[ds]]


def create_bus_lookup_numba(net, bus_is_idx, bus_index, gen_is, eg_is):
    max_bus_idx = np.max(net["bus"].index.values)
    # extract numpy arrays of switch table data
    switch = net["switch"]
    switch_bus = switch["bus"].values
    switch_elm = switch["element"].values
    switch_et_bus = switch["et"].values == "b"
    switch_closed = switch["closed"].values
    # create array for fast checking if a bus is in_service
    bus_in_service = np.zeros(max_bus_idx + 1, dtype=bool)
    bus_in_service[bus_is_idx.values] = True
    # create array for fast checking if a bus is pv bus
    bus_is_pv = np.zeros(max_bus_idx + 1, dtype=bool)
    bus_is_pv[eg_is["bus"].values] = True
    bus_is_pv[gen_is["bus"].values] = True
    if len(net["xward"]) > 0:
        bus_is_pv[net["xward"][net["xward"].in_service == 1]["ad_bus"].values] = True
    # create array that represents the disjoint set
    ar = np.arange(max_bus_idx + 1)
    ds_create(ar, switch_bus, switch_elm, switch_et_bus, switch_closed, bus_is_pv, bus_in_service)
    # finally create and fill bus lookup
    bus_lookup = -np.ones(max_bus_idx + 1, dtype=int)
    fill_bus_lookup(ar, bus_lookup, bus_index)
    return bus_lookup


class DisjointSet(dict):
    def add(self, item):
        self[item] = item

    def find(self, item):
        parent = self[item]
        if self[parent] != parent:
            parent = self.find(parent)
            self[item] = parent
        return parent

    def union(self, item1, item2):
        p1 = self.find(item1)
        p2 = self.find(item2)
        self[p1] = p2


def create_bus_lookup(net, n_bus, bus_index, bus_is, gen_is, eg_is, r_switch):
    # create a mapping from arbitrary pp-index to a consecutive index starting at zero (ppc-index)
    consec_buses = np.arange(n_bus)
    # bus_lookup as dict:
    # bus_lookup = dict(zip(bus_index, consec_buses))

    # bus lookup as mask from pandapower -> pypower
    bus_lookup = -np.ones(max(bus_index) + 1, dtype=int)
    bus_lookup[bus_index] = consec_buses

    # if there are any closed bus-bus switches update those entries
    slidx = ((net["switch"]["closed"].values == 1) &
             (net["switch"]["et"].values == "b") &
             (net["switch"]["bus"].isin(bus_is.index).values) &
             (net["switch"]["element"].isin(bus_is.index).values))
    net._closed_bb_switches = slidx

    if r_switch == 0 and slidx.any():
        # Note: this might seem a little odd - first constructing a pp to ppc mapping without
        # fused busses and then update the entries. The alternative (to construct the final
        # mapping at once) would require to determine how to fuse busses and which busses
        # are not part of any opened bus-bus switches first. It turns out, that the latter takes
        # quite some time in the average usecase, where #busses >> #bus-bus switches.

        # Find PV / Slack nodes -> their bus must be kept when fused with a PQ node
        if len(net["xward"]) > 0:
            # add xwards if available
            pv_ref = set(np.r_[eg_is["bus"].values, gen_is["bus"].values, net["xward"][
                net["xward"].in_service == 1]["ad_bus"].values].flatten())
        else:
            pv_ref = set(np.r_[eg_is["bus"].values, gen_is["bus"].values].flatten())

        # get the pp-indices of the buses which are connected to a switch
        fbus = net["switch"]["bus"].values[slidx]
        tbus = net["switch"]["element"].values[slidx]

        # create a mapping to map each bus to itself at frist ...
        ds = DisjointSet({e: e for e in chain(fbus, tbus)})

        # ... to follow each bus along a possible chain of switches to its final bus and update the
        # map
        for f, t in zip(fbus, tbus):
            ds.union(f, t)

        # now we can find out how to fuse each bus by looking up the final bus of the chain they
        # are connected to
        v = defaultdict(set)
        for a in ds:
            v[ds.find(a)].add(a)
        # build sets of buses which will be fused
        disjoint_sets = [e for e in v.values() if len(e) > 1]

        # check if PV buses need to be fused
        # if yes: the sets with PV buses must be found (which is slow)
        # if no: the check can be omitted
        if any(i in fbus or i in tbus for i in pv_ref):
            # for every disjoint set
            for dj in disjoint_sets:
                # check if pv buses are in the disjoint set dj
                pv_buses_in_set = pv_ref & dj
                nr_pv_bus = len(pv_buses_in_set)
                if nr_pv_bus == 0:
                    # no pv buses. Use any bus in dj
                    map_to = bus_lookup[dj.pop()]
                elif nr_pv_bus == 1:
                    # one pv bus. Get bus from pv_buses_in_set
                    map_to = bus_lookup[pv_buses_in_set.pop()]
                else:
                    raise UserWarning("Can't fuse two PV buses")
                for bus in dj:
                    # update lookup
                    bus_lookup[bus] = map_to
        else:
            # no PV buses in set
            for dj in disjoint_sets:
                # use any bus in set
                map_to = bus_lookup[dj.pop()]
                for bus in dj:
                    # update bus lookup
                    bus_lookup[bus] = map_to
    return bus_lookup


def _build_bus_ppc(net, ppc):
    """
    Generates the ppc["bus"] array and the lookup pandapower indices -> ppc indices
    """
    copy_constraints_to_ppc = net["_options"]["copy_constraints_to_ppc"]
    r_switch = net["_options"]["r_switch"]
    init = net["_options"]["init"]
    mode = net["_options"]["mode"]
    numba = net["_options"]["numba"] if "numba" in net["_options"] else False

    # get bus indices
    bus_index = net["bus"].index.values
    n_bus = len(bus_index)
    # get in service elements
    _is_elements = net["_is_elements"]
    eg_is = _is_elements['ext_grid']
    gen_is = _is_elements['gen']
    bus_is = _is_elements['bus']

    if numba and not r_switch:
        bus_lookup = create_bus_lookup_numba(net, bus_is.index, bus_index, gen_is, eg_is)
    else:
        bus_lookup = create_bus_lookup(net, n_bus, bus_index, bus_is, gen_is, eg_is, r_switch)
    # init ppc with empty values
    ppc["bus"] = np.zeros(shape=(n_bus, 15), dtype=float)
    ppc["bus"][:] = np.array([0, 1, 0, 0, 0, 0, 1, 1, 0, 0, 1, 1.1, 0.9, 0., 0.])
    # apply consecutive bus numbers
    ppc["bus"][:, BUS_I] = np.arange(n_bus)

    # init voltages from net
    ppc["bus"][:n_bus, BASE_KV] = net["bus"]["vn_kv"].values
    # set buses out of service (BUS_TYPE == 4)
    ppc["bus"][bus_lookup[net["bus"].index.values[~net["bus"]["in_service"].values.astype(bool)]],
        BUS_TYPE] = NONE

    if init == "results" and len(net["res_bus"]) > 0:
        # init results (= voltages) from previous power flow
        ppc["bus"][:n_bus, VM] = net["res_bus"]["vm_pu"].values
        ppc["bus"][:n_bus, VA] = net["res_bus"].va_degree.values

    if mode == "sc":
        ppc["bus_sc"] = np.empty(shape=(n_bus, 10), dtype=float)
        ppc["bus_sc"].fill(np.nan)
        _add_c_to_ppc(net, ppc)


    if copy_constraints_to_ppc:
        if "max_vm_pu" in net.bus:
            ppc["bus"][:n_bus, VMAX] = net["bus"].max_vm_pu.values
        else:
            ppc["bus"][:n_bus, VMAX] = 10
        if "min_vm_pu" in net.bus:
            ppc["bus"][:n_bus, VMIN] = net["bus"].min_vm_pu.values
        else:
            ppc["bus"][:n_bus, VMIN] = 0

    net["_pd2ppc_lookups"]["bus"] = bus_lookup


def _calc_loads_and_add_on_ppc(net, ppc):
    '''
    wrapper function to call either the PF or the OPF version
    '''
    mode = net["_options"]["mode"]

    if mode=="opf":
        _calc_loads_and_add_on_ppc_opf(net, ppc)
    else:
        _calc_loads_and_add_on_ppc_pf(net, ppc)


def _calc_loads_and_add_on_ppc_pf(net, ppc):
    # init values
    b, p, q = np.array([], dtype=int), np.array([]), np.array([])

    # get in service elements
    _is_elements = net["_is_elements"]

    l = net["load"]

    # element_is = check if element is at a bus in service & element is in service
    if len(l) > 0:
        voltage_depend_loads = net["_options"]["voltage_depend_loads"]
        if voltage_depend_loads:
            cz = l["const_z_percent"].values / 100.
            ci = l["const_i_percent"].values / 100.
            if ((cz + ci) > 1).any():
                raise ValueError("const_z_percent + const_i_percent need to be less or equal to 100%!")

            # cumulative sum of constant-current loads
            vl = _is_elements["load"] * l["scaling"].values.T * ci / np.float64(1000.)
            p_ci = l["p_kw"].values * vl
            q_ci = l["q_kvar"].values * vl
            b_ci = l["bus"].values

            bus_lookup = net["_pd2ppc_lookups"]["bus"]
            b_ci = bus_lookup[b_ci]
            b_ci, vp_ci, vq_ci = _sum_by_group(b_ci, p_ci, q_ci)

            ppc["bus"][b_ci, PCID] = vp_ci
            ppc["bus"][b_ci, QCID] = vq_ci

        else:
            cz = 0
            ci = 0

        cp = (1 - cz - ci)
        vl = _is_elements["load"] * l["scaling"].values.T * cp / np.float64(1000.)
        q = np.hstack([q, l["q_kvar"].values * vl])
        p = np.hstack([p, l["p_kw"].values * vl])
        b = np.hstack([b, l["bus"].values])


    s = net["sgen"]
    if len(s) > 0:
        vl = _is_elements["sgen"] * s["scaling"].values.T / np.float64(1000.)
        q = np.hstack([q, s["q_kvar"].values * vl])
        p = np.hstack([p, s["p_kw"].values * vl])
        b = np.hstack([b, s["bus"].values])

    w = net["ward"]
    if len(w) > 0:
        vl = _is_elements["ward"] / np.float64(1000.)
        q = np.hstack([q, w["qs_kvar"].values * vl])
        p = np.hstack([p, w["ps_kw"].values * vl])
        b = np.hstack([b, w["bus"].values])

    xw = net["xward"]
    if len(xw) > 0:
        vl = _is_elements["xward"] / np.float64(1000.)
        q = np.hstack([q, xw["qs_kvar"].values * vl])
        p = np.hstack([p, xw["ps_kw"].values * vl])
        b = np.hstack([b, xw["bus"].values])

    # if array is not empty
    if b.size:
        bus_lookup = net["_pd2ppc_lookups"]["bus"]
        b = bus_lookup[b]
        b, vp, vq = _sum_by_group(b, p, q)

        ppc["bus"][b, PD] = vp
        ppc["bus"][b, QD] = vq


def _calc_loads_and_add_on_ppc_opf(net, ppc):
    """ we need to exclude controllable sgens from the bus table
    """
    bus_lookup = net["_pd2ppc_lookups"]["bus"]
    # get in service elements
    _is_elements = net["_is_elements"]

    l = net["load"]
    if not l.empty:
        l["controllable"] = _controllable_to_bool(l["controllable"])
        vl = (_is_elements["load"] & ~l["controllable"]) * l["scaling"].values.T / np.float64(1000.)
        lp = l["p_kw"].values * vl
        lq = l["q_kvar"].values * vl
    else:
        lp = []
        lq = []

    sgen = net["sgen"]
    if not sgen.empty:
        sgen["controllable"] = _controllable_to_bool(sgen["controllable"])
        vl = (_is_elements["sgen"] & ~sgen["controllable"]) * sgen["scaling"].values.T / \
             np.float64(1000.)
        sp = sgen["p_kw"].values * vl
        sq = sgen["q_kvar"].values * vl
    else:
        sp = []
        sq = []

    b = bus_lookup[np.hstack([l["bus"].values, sgen["bus"].values])]
    b, vp, vq = _sum_by_group(b, np.hstack([lp, sp]), np.hstack([lq, sq]))

    ppc["bus"][b, PD] = vp
    ppc["bus"][b, QD] = vq


def _calc_shunts_and_add_on_ppc(net, ppc):
    # init values
    b, p, q = np.array([], dtype=int), np.array([]), np.array([])
    # get in service elements
    _is_elements = net["_is_elements"]

    s = net["shunt"]
    if len(s) > 0:
        vl = _is_elements["shunt"] / np.float64(1000.)
        q = np.hstack([q, s["q_kvar"].values * vl])
        p = np.hstack([p, s["p_kw"].values * vl])
        b = np.hstack([b, s["bus"].values])

    w = net["ward"]
    if len(w) > 0:
        vl = _is_elements["ward"] / np.float64(1000.)
        q = np.hstack([q, w["qz_kvar"].values * vl])
        p = np.hstack([p, w["pz_kw"].values * vl])
        b = np.hstack([b, w["bus"].values])

    xw = net["xward"]
    if len(xw) > 0:
        vl = _is_elements["xward"] / np.float64(1000.)
        q = np.hstack([q, xw["qz_kvar"].values * vl])
        p = np.hstack([p, xw["pz_kw"].values * vl])
        b = np.hstack([b, xw["bus"].values])

    # constant-impedance loads if voltage_depend_loads=True
    l = net["load"]
    voltage_depend_loads = net["_options"]["voltage_depend_loads"]
    if len(l) > 0 and voltage_depend_loads:
        cz = l["const_z_percent"].values / 100.
        vl = _is_elements["load"] * l["scaling"].values.T * cz / np.float64(1000.)
        q = np.hstack([q, l["q_kvar"].values * vl])
        p = np.hstack([p, l["p_kw"].values * vl])
        b = np.hstack([b, l["bus"].values])

    # if array is not empty
    if b.size:
        bus_lookup = net["_pd2ppc_lookups"]["bus"]
        b = bus_lookup[b]
        b, vp, vq = _sum_by_group(b, p, q)

        ppc["bus"][b, GS] = vp
        ppc["bus"][b, BS] = -vq

def _controllable_to_bool(ctrl):
    ctrl_bool = []
    for val in ctrl:
        ctrl_bool.append(val if not np.isnan(val) else False)
    return np.array(ctrl_bool, dtype=bool)

def _add_gen_impedances_ppc(net, ppc):
    _add_ext_grid_sc_impedance(net, ppc)
    _add_gen_sc_impedance(net, ppc)
    if net._options["consider_sgens"] and net._options["fault"] != "2ph":
        _add_sgen_sc_impedance(net, ppc)

def _add_ext_grid_sc_impedance(net, ppc):
    from pandapower.shortcircuit.idx_bus import C_MAX, C_MIN
    bus_lookup = net["_pd2ppc_lookups"]["bus"]
    case = net._options["case"]
    eg = net._is_elements["ext_grid"]
    if len(eg) == 0:
        return
    eg_buses = eg.bus.values
    eg_buses_ppc  = bus_lookup[eg_buses]

    c = ppc["bus_sc"][eg_buses_ppc, C_MAX] if case == "max" else ppc["bus_sc"][eg_buses_ppc, C_MIN]
    if not "s_sc_%s_mva"%case in eg:
        raise ValueError("short circuit apparent power s_sc_%s_mva needs to be specified for external grid"%case)
    s_sc = eg["s_sc_%s_mva"%case].values
    if not "rx_%s"%case in eg:
        raise ValueError("short circuit R/X rate rx_%s needs to be specified for external grid"%case)
    rx = eg["rx_%s"%case].values

    z_grid = c / s_sc
    x_grid = np.sqrt(z_grid**2 / (rx**2 + 1))
    r_grid = np.sqrt(z_grid**2 - x_grid**2)
    eg["r"] = r_grid
    eg["x"] = x_grid

    y_grid = 1 / (r_grid + x_grid*1j)
    buses, gs, bs = _sum_by_group(eg_buses_ppc, y_grid.real, y_grid.imag)
    ppc["bus"][buses, GS] = gs
    ppc["bus"][buses, BS] = bs

def _add_gen_sc_impedance(net, ppc):
    from pandapower.shortcircuit.idx_bus import C_MAX
    gen = net._is_elements["gen"]
    if len(gen) == 0:
        return
    gen_buses = gen.bus.values
    bus_lookup = net["_pd2ppc_lookups"]["bus"]
    gen_buses_ppc = bus_lookup[gen_buses]

    vn_net = ppc["bus"][gen_buses_ppc, BASE_KV]
    cmax = ppc["bus_sc"][gen_buses_ppc, C_MAX]
    phi_gen = np.arccos(gen.cos_phi)

    vn_gen = gen.vn_kv.values
    sn_gen = gen.sn_kva.values

    z_r = vn_net**2 / sn_gen * 1e3
    x_gen = gen.xdss.values / 100 * z_r
    r_gen = gen.rdss.values / 100 * z_r

    kg = _generator_correction_factor(vn_net, vn_gen, cmax, phi_gen, gen.xdss)
    y_gen = 1 / ((r_gen + x_gen*1j) * kg)

    buses, gs, bs = _sum_by_group(gen_buses_ppc, y_gen.real, y_gen.imag)
    ppc["bus"][buses, GS] = gs
    ppc["bus"][buses, BS] = bs

def _add_sgen_sc_impedance(net, ppc):
    bus_lookup = net["_pd2ppc_lookups"]["bus"]
    sgen = net.sgen[net._is_elements["sgen"]]
    if len(sgen) == 0:
        return
    if any(pd.isnull(sgen.sn_kva)):
        raise UserWarning("sn_kva needs to be specified for all sgens in net.sgen.sn_kva")
    sgen_buses = sgen.bus.values
    sgen_buses_ppc = bus_lookup[sgen_buses]

    z_sgen = 1 / (sgen.sn_kva.values * 1e-3) / 3 #1 us reference voltage in pu
    x_sgen = np.sqrt(z_sgen**2 / (0.1**2 + 1))
    r_sgen = np.sqrt(z_sgen**2 - x_sgen**2)
    y_sgen = 1 / (r_sgen + x_sgen*1j)

    buses, gs, bs = _sum_by_group(sgen_buses_ppc, y_sgen.real, y_sgen.imag)
    ppc["bus"][buses, GS] = gs
    ppc["bus"][buses, BS] = bs

def _generator_correction_factor(vn_net, vn_gen, cmax, phi_gen, xdss):
    kg = vn_gen / vn_net * cmax / (1 + xdss * np.sin(phi_gen))
    return kg

def _add_c_to_ppc(net, ppc):
    from pandapower.shortcircuit.idx_bus import C_MAX, C_MIN
    ppc["bus_sc"][:, C_MAX] = 1.1
    ppc["bus_sc"][:, C_MIN] = 1.
    lv_buses = np.where(ppc["bus"][:, BASE_KV] < 1.)
    if len(lv_buses) > 0:
        lv_tol_percent = net["_options"]["lv_tol_percent"]
        if lv_tol_percent == 10:
            c_ns = 1.1
        elif lv_tol_percent == 6:
            c_ns = 1.05
        else:
            raise ValueError("Voltage tolerance in the low voltage grid has" \
                                        " to be either 6% or 10% according to IEC 60909")
        ppc["bus_sc"][lv_buses, C_MAX] = c_ns
        ppc["bus_sc"][lv_buses, C_MIN] = .95