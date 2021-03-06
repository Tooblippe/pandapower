# -*- coding: utf-8 -*-

# Copyright (c) 2016-2017 by University of Kassel and Fraunhofer Institute for Wind Energy and
# Energy System Technology (IWES), Kassel. All rights reserved. Use of this source code is governed
# by a BSD-style license that can be found in the LICENSE file.
import copy
from collections import defaultdict

import numpy as np
import pandas as pd

from pandapower.auxiliary import get_indices, pandapowerNet
from pandapower.create import create_empty_network, create_piecewise_linear_cost
from pandapower.topology import unsupplied_buses
from pandapower.run import runpp
from pandapower import __version__

try:
    import pplog as logging
except:
    import logging

logger = logging.getLogger(__name__)


# --- Information
def lf_info(net, numv=1, numi=2): # pragma: no cover
    """
    Prints some basic information of the results in a net
    (max/min voltage, max trafo load, max line load).

    OPTIONAL:

        **numv** (integer, 1) - maximal number of printed maximal respectively minimal voltages

        **numi** (integer, 2) - maximal number of printed maximal loading at trafos or lines
    """
    logger.info("Max voltage")
    for _, r in net.res_bus.sort_values("vm_pu", ascending=False).iloc[:numv].iterrows():
        logger.info("  %s at busidx %s (%s)", r.vm_pu, r.name, net.bus.name.at[r.name])
    logger.info("Min voltage")
    for _, r in net.res_bus.sort_values("vm_pu").iloc[:numv].iterrows():
        logger.info("  %s at busidx %s (%s)", r.vm_pu, r.name, net.bus.name.at[r.name])
    logger.info("Max loading trafo")
    if net.res_trafo is not None:
        for _, r in net.res_trafo.sort_values("loading_percent", ascending=False).iloc[
                    :numi].iterrows():
            logger.info("  %s loading at trafo %s (%s)", r.loading_percent, r.name,
                        net.trafo.name.at[r.name])
    logger.info("Max loading line")
    for _, r in net.res_line.sort_values("loading_percent", ascending=False).iloc[:numi].iterrows():
        logger.info("  %s loading at line %s (%s)", r.loading_percent, r.name,
                    net.line.name.at[r.name])


def opf_task(net): # pragma: no cover
    """
    Prints some basic inforamtion of the optimal powerflow task.
    """
    logger.info("Cotrollables & Costs:")
    logger.info("  External Grid")
    for q, r in net.ext_grid.iterrows():
        if "cost_per_kw" in net.ext_grid.columns:
            logger.info("    %i at Node %i with cost %s", q, r.bus, r.cost_per_kw)
        else:
            logger.info("    at Node %i", r.bus)
    if 'controllable' in net.gen.columns:
        if (net.gen.controllable == True).any():
            logger.info("  Generator")
            if "cost_per_kw" in net.gen.columns:
                for q, r in net.gen[net.gen.controllable == True].iterrows():
                    logger.info("    %i at Node %i with cost %s", q, r.bus, r.cost_per_kw)
            else:
                for q, r in net.gen[net.gen.controllable == True].iterrows():
                    logger.info("    at Node %i", r.bus)
    if 'controllable' in net.sgen.columns:
        if (net.sgen.controllable == True).any():
            logger.info("  Static Generator")
            if "cost_per_kw" in net.sgen.columns:
                for q, r in net.sgen[net.sgen.controllable == True].iterrows():
                    logger.info("    %i at Node %i with cost %s", q, r.bus, r.cost_per_kw)
            else:
                logger.info("    at Node %i", r.bus)
    logger.info("Constraints:")
    c_exist = False  # stores if there are any constraints
    # --- Generator constraints
    c_gen_columns = pd.Series(['min_p_kw', 'max_p_kw', 'min_q_kvar', 'max_q_kvar'])
    c_gen_columns_exist = c_gen_columns[c_gen_columns.isin(net.gen.columns)]
    c_gen = net.gen[c_gen_columns_exist].dropna(how='all')
    if (c_gen.shape[1] > 0) & (c_gen.shape[0] > 0):
        c_exist = True
        logger.info("  Generator Constraints")
        for i in c_gen_columns[c_gen_columns.isin(net.gen.columns) == False]:
            c_gen[i] = np.nan
        if (c_gen.max_p_kw <= c_gen.min_p_kw).any():
            logger.warn("The value of min_p_kw must be less than max_p_kw for all generators. " +
                        "Please observe the pandapower signing system.")
        if (c_gen.min_q_kvar >= c_gen.max_q_kvar).any():
            logger.warn("The value of min_q_kvar must be less than max_q_kvar for all generators. " +
                        "Please observe the pandapower signing system.")
        if c_gen.duplicated()[1:].all():
            logger.info("    at all Gens [min_p_kw, max_p_kw, min_q_kvar, max_q_kvar] is " +
                        "[%s, %s, %s, %s]", c_gen.min_p_kw[0], c_gen.max_p_kw[0],
                        c_gen.min_q_kvar[0], c_gen.max_q_kvar[0])
        else:
            unique_rows = ~c_gen.duplicated()
            duplicated_rows = c_gen.duplicated()
            for i in c_gen[unique_rows].index:
                same_data_gens = list([i])
                for i2 in c_gen[duplicated_rows].index:
                    if c_gen.iloc[i].equals(c_gen.iloc[i2]):
                        same_data_gens.append(i2)
                logger.info('    at Gens %s [min_p_kw, max_p_kw, min_q_kvar, max_q_kvar] ' +
                            'is [%s, %s, %s, %s]', ', '.join(map(str, same_data_gens)),
                            c_gen.min_p_kw[i], c_gen.max_p_kw[i],
                            c_gen.min_q_kvar[i], c_gen.max_q_kvar[i])
    # --- Static Generator constraints
    c_sgen_columns = pd.Series(['min_p_kw', 'max_p_kw', 'min_q_kvar', 'max_q_kvar'])
    c_sgen_columns_exist = c_sgen_columns[c_sgen_columns.isin(net.sgen.columns)]
    c_sgen = net.sgen[c_sgen_columns_exist].dropna(how='all')
    if (c_sgen.shape[1] > 0) & (c_sgen.shape[0] > 0):
        c_exist = True
        logger.info("  Static Generator Constraints")
        for i in c_sgen_columns[c_sgen_columns.isin(net.sgen.columns) == False]:
            c_sgen[i] = np.nan
        if (c_sgen.max_p_kw <= c_sgen.min_p_kw).any():
            logger.warn("The value of min_p_kw must be less than max_p_kw for all static " +
                        "generators. Please observe the pandapower signing system.")
        if (c_sgen.min_q_kvar >= c_sgen.max_q_kvar).any():
            logger.warn("The value of min_q_kvar must be less than max_q_kvar for all static.  " +
                        "generators. Please observe the pandapower signing system.")
        if c_sgen.duplicated()[1:].all():
            logger.info("    at all Sgens [min_p_kw, max_p_kw, min_q_kvar, max_q_kvar] is " +
                        "[%s, %s, %s, %s]", c_sgen.min_p_kw[0], c_sgen.max_p_kw[0],
                        c_sgen.min_q_kvar[0], c_sgen.max_q_kvar[0])
        else:
            unique_rows = ~c_sgen.duplicated()
            duplicated_rows = c_sgen.duplicated()
            for i in c_sgen[unique_rows].index:
                same_data_sgens = list([i])
                for i2 in c_sgen[duplicated_rows].index:
                    if c_sgen.iloc[i].equals(c_sgen.iloc[i2]):
                        same_data_sgens.append(i2)
                logger.info('    at Sgens %s [min_p_kw, max_p_kw, min_q_kvar, max_q_kvar]' +
                            'is [%s, %s, %s, %s]', ', '.join(map(str, same_data_sgens)),
                            c_sgen.min_p_kw[i], c_sgen.max_p_kw[i],
                            c_sgen.min_q_kvar[i], c_sgen.max_q_kvar[i])
    # --- Voltage constraints
    if pd.Series(['min_vm_pu', 'max_vm_pu']).isin(net.bus.columns).any():
        c_bus = net.bus[['min_vm_pu', 'max_vm_pu']].dropna(how='all')
        if c_bus.shape[0] > 0:
            c_exist = True
            logger.info("  Voltage Constraints")
            if (net.bus.min_vm_pu >= net.bus.max_vm_pu).any():
                logger.warn("The value of min_vm_pu must be less than max_vm_pu.")
            if c_bus.duplicated()[1:].all():
                logger.info('    at all Nodes [min_vm_pu, max_vm_pu] is [%s, %s]',
                            c_bus.min_vm_pu[0], c_bus.max_vm_pu[0])
            else:
                unique_rows = ~c_bus.duplicated()
                duplicated_rows = c_bus.duplicated()
                for i in c_bus[unique_rows].index:
                    same_data_nodes = list([i])
                    for i2 in c_bus[duplicated_rows].index:
                        if c_bus.iloc[i].equals(c_bus.iloc[i2]):
                            same_data_nodes.append(i2)
                    logger.info('    at Nodes %s [min_vm_pu, max_vm_pu] is [%s, %s]',
                                ', '.join(map(str, same_data_nodes)), c_bus.min_vm_pu[i],
                                c_bus.max_vm_pu[i])
    # --- Trafo constraints
    if "max_loading_percent" in net.trafo.columns:
        c_trafo = net.trafo['max_loading_percent'].dropna()
        if c_trafo.shape[0] > 0:
            c_exist = True
            logger.info("  Trafo Constraint")
            if c_trafo.duplicated()[1:].all():
                logger.info('    at all Trafos max_loading_percent is %s', c_trafo[0])
            else:
                unique_rows = ~c_bus.duplicated()
                duplicated_rows = c_bus.duplicated()
                for i in c_trafo[unique_rows].index:
                    same_data_trafos = list([i])
                    for i2 in c_trafo[duplicated_rows].index:
                        if c_trafo.iloc[i].equals(c_trafo.iloc[i2]):
                            same_data_trafos.append(i2)
                    logger.info("    at Trafos %s max_loading_percent is %s",
                                ', '.join(map(str, same_data_trafos)), c_trafo[i])
    # --- Line constraints
    if "max_loading_percent" in net.line.columns:
        c_line = net.line['max_loading_percent'].dropna()
        if c_line.shape[0] > 0:
            c_exist = True
            logger.info("  Line Constraint")
            if c_line.duplicated()[1:].all():
                logger.info('    at all Lines max_loading_percent is %s', c_line[0])
            else:
                unique_rows = ~c_bus.duplicated()
                duplicated_rows = c_bus.duplicated()
                for i in c_line[unique_rows].index:
                    same_data_lines = list([i])
                    for i2 in c_line[duplicated_rows].index:
                        if c_line.iloc[i].equals(c_line.iloc[i2]):
                            same_data_lines.append(i2)
                    logger.info("    at Lines %s max_loading_percent is %s",
                                ', '.join(map(str, same_data_lines)), c_line[i])

    if not c_exist:
        logger.info("  There are no constraints.")


        # check if full range of generator is covered by pwl cost function!


def switch_info(net, sidx): # pragma: no cover
    """
    Prints what buses and elements are connected by a certain switch.
    """
    switch_type = net.switch.at[sidx, "et"]
    bidx = net.switch.at[sidx, "bus"]
    bus_name = net.bus.at[bidx, "name"]
    eidx = net.switch.at[sidx, "element"]
    if switch_type == "b":
        bus2_name = net.bus.at[eidx, "name"]
        logger.info("Switch %u connects bus %u (%s) with bus %u (%s)" % (sidx, bidx, bus_name,
                                                                         eidx, bus2_name))
    elif switch_type == "l":
        line_name = net.line.at[eidx, "name"]
        logger.info("Switch %u connects bus %u (%s) with line %u (%s)" % (sidx, bidx, bus_name,
                                                                          eidx, line_name))
    elif switch_type == "t":
        trafo_name = net.trafo.at[eidx, "name"]
        logger.info("Switch %u connects bus %u (%s) with trafo %u (%s)" % (sidx, bidx, bus_name,
                                                                           eidx, trafo_name))


def overloaded_lines(net, max_load=100):
    """
    Returns the results for all lines with loading_percent > max_load or None, if
    there are none.
    """
    if net.converged:
        return net["res_line"].index[net["res_line"]["loading_percent"] > max_load]
    else:
        raise UserWarning("The last loadflow terminated erratically, results are invalid!")


def violated_buses(net, min_vm_pu, max_vm_pu):
    """
    Returns all bus indices where vm_pu is not within min_vm_pu and max_vm_pu or returns None, if
    there are none of those buses.
    """
    if net.converged:
        return net["bus"].index[(net["res_bus"]["vm_pu"] < min_vm_pu) |
                                (net["res_bus"]["vm_pu"] > max_vm_pu)]
    else:
        raise UserWarning("The last loadflow terminated erratically, results are invalid!")


def nets_equal(x, y, check_only_results=False, tol=1.e-14):
    """
    Compares the DataFrames of two networks. The networks are considered equal
    if they share the same keys and values, except of the
    'et' (elapsed time) entry which differs depending on
    runtime conditions and entries stating with '_'.
    """
    eq = True
    not_equal = []

    if isinstance(x, pandapowerNet) and isinstance(y, pandapowerNet):
        # for two networks make sure both have the same keys that do not start with "_"...
        x_keys = [key for key in x.keys() if not key.startswith("_")]
        y_keys = [key for key in y.keys() if not key.startswith("_")]

        if len(set(x_keys) - set(y_keys)) + len(set(y_keys) - set(x_keys)) > 0:
            logger.info("Networks entries mismatch:", x_keys, " - VS. - ", y_keys)
            return False

        # ... and then iter through the keys, checking for equality for each table
        for df_name in x_keys:
            # skip 'et' (elapsed time) and entries starting with '_' (internal vars)
            if (df_name != 'et' and not df_name.startswith("_")):
                if check_only_results and not df_name.startswith("res_"):
                    continue  # skip anything that is not a result table

                if isinstance(x[df_name], pd.DataFrame) and isinstance(y[df_name], pd.DataFrame):
                    frames_equal = dataframes_equal(x[df_name], y[df_name], tol)
                    eq &= frames_equal

                    if not frames_equal:
                        not_equal.append(df_name)

    if len(not_equal) > 0:
        logger.info("Networks do not match in DataFrame(s): %s" % (', '.join(not_equal)))

    return eq


def dataframes_equal(x_df, y_df, tol=1.e-14):
    # eval if two DataFrames are equal, with regard to a tolerance
    if len(x_df) == len(y_df) and len(x_df.columns) == len(y_df.columns):
        # we use numpy.allclose to grant a tolerance on numerical values
        numerical_equal = np.allclose(x_df.select_dtypes(include=[np.number]),
                                      y_df.select_dtypes(include=[np.number]),
                                      atol=tol, equal_nan=True)

        # ... use pandas .equals for the rest, which also evaluates NaNs to be equal
        rest_equal = x_df.select_dtypes(exclude=[np.number]).equals(
            y_df.select_dtypes(exclude=[np.number]))

        return numerical_equal & rest_equal
    else:
        return False


# --- Simulation setup and preparations
def convert_format(net):
    """
    Converts old nets to new format to ensure consistency. The converted net is returned.
    """
    _pre_release_changes(net)
    if not "sn_kva" in net:
        net.sn_kva = 1e3
    net.line.rename(columns={'imax_ka': 'max_i_ka'}, inplace=True)
    for typ, data in net.std_types["line"].items():
        if "imax_ka" in data:
            net.std_types["line"][typ]["max_i_ka"] = net.std_types["line"][typ].pop("imax_ka")
    # unsymmetric impedance
    if "r_pu" in net.impedance:
        net.impedance["rft_pu"] = net.impedance["rtf_pu"] = net.impedance["r_pu"]
        net.impedance["xft_pu"] = net.impedance["xtf_pu"] = net.impedance["x_pu"]
    # initialize measurement dataframe
    if "measurement" in net and "element_type" not in net.measurement:
        if net.measurement.empty:
            del net["measurement"]
        else:
            logger.warn("The measurement structure seems outdated. Please adjust it "
                        "according to the documentation.")
    if "measurement" in net and "name" not in net.measurement:
        net.measurement.insert(0, "name", None)
    if "measurement" not in net:
        net["measurement"] = pd.DataFrame(np.zeros(0, dtype=[("name", np.dtype(object)),
                                                             ("type", np.dtype(object)),
                                                             ("element_type", np.dtype(object)),
                                                             ("value", "f8"),
                                                             ("std_dev", "f8"),
                                                             ("bus", "u4"),
                                                             ("element", np.dtype(object))]))
    if "dcline" not in net:
        net["dcline"] = pd.DataFrame(np.zeros(0, dtype=[("name", np.dtype(object)),
                                                        ("from_bus", "u4"),
                                                        ("to_bus", "u4"),
                                                        ("p_kw", "f8"),
                                                        ("loss_percent", 'f8'),
                                                        ("loss_kw", 'f8'),
                                                        ("vm_from_pu", "f8"),
                                                        ("vm_to_pu", "f8"),
                                                        ("max_p_kw", "f8"),
                                                        ("min_q_from_kvar", "f8"),
                                                        ("min_q_to_kvar", "f8"),
                                                        ("max_q_from_kvar", "f8"),
                                                        ("max_q_to_kvar", "f8"),
                                                        ("cost_per_kw", 'f8'),
                                                        ("in_service", 'bool')]))
    if "_empty_res_dcline" not in net:
        net["_empty_res_dcline"] = pd.DataFrame(np.zeros(0, dtype=[("p_from_kw", "f8"),
                                                                   ("q_from_kvar", "f8"),
                                                                   ("p_to_kw", "f8"),
                                                                   ("q_to_kvar", "f8"),
                                                                   ("pl_kw", "f8"),
                                                                   ("vm_from_pu", "f8"),
                                                                   ("va_from_degree", "f8"),
                                                                   ("vm_to_pu", "f8"),
                                                                   ("va_to_degree", "f8")]))
    if not "version" in net or net.version < 1.1:
        if "min_p_kw" in net.gen and "max_p_kw" in net.gen:
            if np.any(net.gen.min_p_kw > net.gen.max_p_kw):
                pmin = copy.copy(net.gen.min_p_kw.values)
                pmax = copy.copy(net.gen.max_p_kw.values)
                net.gen["min_p_kw"] = pmax
                net.gen["max_p_kw"] = pmin
    if not "piecewise_linear_cost" in net:
        net["piecewise_linear_cost"] = pd.DataFrame(np.zeros(0, dtype=[("type", np.dtype(object)),
                                                                       ("element", np.dtype(object)),
                                                                       ("element_type", np.dtype(object)),
                                                                       ("p", np.dtype(object)),
                                                                       ("f", np.dtype(object))]))

    if not "polynomial_cost" in net:
        net["polynomial_cost"] = pd.DataFrame(np.zeros(0, dtype=[("type", np.dtype(object)),
                                                                 ("element", np.dtype(object)),
                                                                 ("element_type", np.dtype(object)),
                                                                 ("c", np.dtype(object))]))

    if "cost_per_kw" in net.gen:
        for index, cost in net.gen.cost_per_kw.iteritems():
            if not np.isnan(cost):
                p = net.gen.min_p_kw.at[index]
                create_piecewise_linear_cost(net, index, "gen", np.array([[p, cost * p], [0, 0]]))

    if "cost_per_kw" in net.sgen:
        for index, cost in net.sgen.cost_per_kw.iteritems():
            if not np.isnan(cost):
                p = net.sgen.min_p_kw.at[index]
                create_piecewise_linear_cost(net, index, "sgen", np.array([[p, cost * p], [0, 0]]))

    if "cost_per_kw" in net.ext_grid:
        for index, cost in net.ext_grid.cost_per_kw.iteritems():
            if not np.isnan(cost):
                p = net.ext_grid.min_p_kw.at[index]
                create_piecewise_linear_cost(net, index, "ext_grid", np.array([[p, cost * p], [0, 0]]))

    if "cost_per_kvar" in net.gen:
        for index, cost in net.gen.cost_per_kvar.iteritems():
            if not np.isnan(cost):
                qmin = net.gen.min_q_kvar.at[index]
                qmax = net.gen.max_q_kvar.at[index]
                create_piecewise_linear_cost(net, index, "gen",
                                             np.array([[qmin, cost * qmin], [0, 0], [qmax, cost * qmax]]), type="q")

    if "cost_per_kvar" in net.sgen:
        for index, cost in net.sgen.cost_per_kvar.iteritems():
            if not np.isnan(cost):
                qmin = net.sgen.min_q_kvar.at[index]
                qmax = net.sgen.max_q_kvar.at[index]
                create_piecewise_linear_cost(net, index, "sgen",
                                             np.array([[qmin, cost * qmin], [0, 0], [qmax, cost * qmax]]), type="q")

    if "cost_per_kvar" in net.ext_grid:
        for index, cost in net.ext_grid.cost_per_kvar.iteritems():
            if not np.isnan(cost):
                qmin = net.ext_grid.min_q_kvar.at[index]
                qmax = net.ext_grid.max_q_kvar.at[index]
                create_piecewise_linear_cost(net, index, "ext_grid",
                                             np.array([[qmin, cost * qmin], [0, 0], [qmax, cost * qmax]]), type="q")

    if not "tp_st_degree" in net.trafo:
        net.trafo["tp_st_degree"] = np.nan
    if not "_pd2ppc_lookups" in net:
        net._pd2ppc_lookups = {"bus": None,
                               "ext_grid": None,
                               "gen": None}
    if not "_ppc2pd_lookups" in net:
        net._ppc2pd_lookups = {"bus": None,
                               "ext_grid": None,
                               "gen": None}
    if not "_is_elements" in net and "__is_elements" in net:
        net["_is_elements"] = copy.deepcopy(net["__is_elements"])
        net.pop("__is_elements", None)
    elif not "_is_elements" in net and "_is_elems" in net:
        net["_is_elements"] = copy.deepcopy(net["_is_elems"])
        net.pop("_is_elems", None)

    if "options" in net:
        if "recycle" in net["options"]:
            if not "_is_elements" in net["options"]["recycle"]:
                net["options"]["recycle"]["_is_elements"] = copy.deepcopy(net["options"]["recycle"]["is_elems"])
                net["options"]["recycle"].pop("is_elems", None)


    if not "const_z_percent" in net.load or not "const_i_percent" in net.load:
        net.load["const_z_percent"] = np.zeros(net.load.shape[0])
        net.load["const_i_percent"] = np.zeros(net.load.shape[0])

    if not "vn_kv" in net["shunt"]:
        net.shunt["vn_kv"] = net.bus.vn_kv.loc[net.shunt.bus.values].values
    if not "step" in net["shunt"]:
        net.shunt["step"] = 1
    if not "_pd2ppc_lookups" in net:
        net["_pd2ppc_lookups"] = {"bus": None,
                                  "gen": None,
                                  "branch": None}
    net.version = float(__version__[:3])
    return net


def _pre_release_changes(net):
    from pandapower.std_types import add_basic_std_types, create_std_type, parameter_from_std_type
    from pandapower.powerflow import reset_results
    if "std_types" not in net:
        net.std_types = {"line": {}, "trafo": {}, "trafo3w": {}}
        add_basic_std_types(net)

        import os
        import json
        path, file = os.path.split(os.path.realpath(__file__))
        linedb = os.path.join(path, "linetypes.json")
        if os.path.isfile(linedb):
            with open(linedb, 'r') as f:
                lt = json.load(f)
        else:
            lt = {}
        for std_type in net.line.std_type.unique():
            if std_type in lt:
                if "shift_degree" not in lt[std_type]:
                    lt[std_type]["shift_degree"] = 0
                create_std_type(net, lt[std_type], std_type, element="line")
        trafodb = os.path.join(path, "trafotypes.json")
        if os.path.isfile(trafodb):
            with open(trafodb, 'r') as f:
                tt = json.load(f)
        else:
            tt = {}
        for std_type in net.trafo.std_type.unique():
            if std_type in tt:
                create_std_type(
                    net, tt[std_type], std_type, element="trafo")

    net.trafo.tp_side.replace(1, "hv", inplace=True)
    net.trafo.tp_side.replace(2, "lv", inplace=True)
    net.trafo.tp_side = net.trafo.tp_side.where(pd.notnull(net.trafo.tp_side), None)
    net.trafo3w.tp_side.replace(1, "hv", inplace=True)
    net.trafo3w.tp_side.replace(2, "mv", inplace=True)
    net.trafo3w.tp_side.replace(3, "lv", inplace=True)
    net.trafo3w.tp_side = net.trafo3w.tp_side.where(pd.notnull(net.trafo3w.tp_side), None)

    net["bus"] = net["bus"].rename(
        columns={'voltage_level': 'vn_kv', 'bus_type': 'type', "un_kv": "vn_kv"})
    net["bus"]["type"].replace("s", "b", inplace=True)
    net["bus"]["type"].replace("k", "n", inplace=True)
    net["line"] = net["line"].rename(columns={'vf': 'df', 'line_type': 'type'})
    net["ext_grid"] = net["ext_grid"].rename(columns={"angle_degree": "va_degree",
                                                      "ua_degree": "va_degree", "sk_max_mva": "s_sc_max_mva",
                                                      "sk_min_mva": "s_sc_min_mva"})
    net["line"]["type"].replace("f", "ol", inplace=True)
    net["line"]["type"].replace("k", "cs", inplace=True)
    net["trafo"] = net["trafo"].rename(columns={'trafotype': 'std_type', "type": "std_type",
                                                "un1_kv": "vn_hv_kv", "un2_kv": "vn_lv_kv",
                                                'vfe_kw': 'pfe_kw', "unh_kv": "vn_hv_kv",
                                                "unl_kv": "vn_lv_kv", 'trafotype': 'std_type',
                                                "type": "std_type", 'vfe_kw': 'pfe_kw',
                                                "uk_percent": "vsc_percent",
                                                "ur_percent": "vscr_percent",
                                                "vnh_kv": "vn_hv_kv", "vnl_kv": "vn_lv_kv"})
    net["trafo3w"] = net["trafo3w"].rename(columns={"unh_kv": "vn_hv_kv", "unm_kv": "vn_mv_kv",
                                                    "unl_kv": "vn_lv_kv",
                                                    "ukh_percent": "vsc_hv_percent",
                                                    "ukm_percent": "vsc_mv_percent",
                                                    "ukl_percent": "vsc_lv_percent",
                                                    "urh_percent": "vscr_hv_percent",
                                                    "urm_percent": "vscr_mv_percent",
                                                    "url_percent": "vscr_lv_percent",
                                                    "vnh_kv": "vn_hv_kv", "vnm_kv": "vn_mv_kv",
                                                    "vnl_kv": "vn_lv_kv", "snh_kv": "sn_hv_kv",
                                                    "snm_kv": "sn_mv_kv", "snl_kv": "sn_lv_kv"})
    if "name" not in net.switch.columns:
        net.switch["name"] = None
    net["switch"] = net["switch"].rename(columns={'element_type': 'et'})
    net["ext_grid"] = net["ext_grid"].rename(columns={'voltage': 'vm_pu', "u_pu": "vm_pu",
                                                      "sk_max": "sk_max_mva", "ua_degree": "va_degree"})
    if "in_service" not in net["ext_grid"].columns:
        net["ext_grid"]["in_service"] = 1
    if "shift_mv_degree" not in net["trafo3w"].columns:
        net["trafo3w"]["shift_mv_degree"] = 0
    if "shift_lv_degree" not in net["trafo3w"].columns:
        net["trafo3w"]["shift_lv_degree"] = 0
    parameter_from_std_type(net, "shift_degree", element="trafo", fill=0)
    if "gen" not in net:
        net["gen"] = pd.DataFrame(np.zeros(0, dtype=[("name", np.dtype(object)),
                                                     ("bus", "u4"),
                                                     ("p_kw", "f8"),
                                                     ("vm_pu", "f8"),
                                                     ("sn_kva", "f8"),
                                                     ("scaling", "f8"),
                                                     ("in_service", "i8"),
                                                     ("min_q_kvar", "f8"),
                                                     ("max_q_kvar", "f8"),
                                                     ("type", np.dtype(object))]))

    if "impedance" not in net:
        net["impedance"] = pd.DataFrame(np.zeros(0, dtype=[("name", np.dtype(object)),
                                                           ("from_bus", "u4"),
                                                           ("to_bus", "u4"),
                                                           ("r_pu", "f8"),
                                                           ("x_pu", "f8"),
                                                           ("sn_kva", "f8"),
                                                           ("in_service", 'bool')]))
    if "ward" not in net:
        net["ward"] = pd.DataFrame(np.zeros(0, dtype=[("name", np.dtype(object)),
                                                      ("bus", "u4"),
                                                      ("ps_kw", "u4"),
                                                      ("qs_kvar", "f8"),
                                                      ("pz_kw", "f8"),
                                                      ("qz_kvar", "f8"),
                                                      ("in_service", "f8")]))
    if "xward" not in net:
        net["xward"] = pd.DataFrame(np.zeros(0, dtype=[("name", np.dtype(object)),
                                                       ("bus", "u4"),
                                                       ("ps_kw", "u4"),
                                                       ("qs_kvar", "f8"),
                                                       ("pz_kw", "f8"),
                                                       ("qz_kvar", "f8"),
                                                       ("r_ohm", "f8"),
                                                       ("x_ohm", "f8"),
                                                       ("vm_pu", "f8"),
                                                       ("in_service", "f8")]))
    if "shunt" not in net:
        net["shunt"] = pd.DataFrame(np.zeros(0, dtype=[("bus", "u4"),
                                                       ("name", np.dtype(object)),
                                                       ("p_kw", "f8"),
                                                       ("q_kvar", "f8"),
                                                       ("scaling", "f8"),
                                                       ("in_service", "i8")]))

    if "parallel" not in net.line:
        net.line["parallel"] = 1
    if "parallel" not in net.trafo:
        net.trafo["parallel"] = 1
    if "_empty_res_bus" not in net:
        net2 = create_empty_network()
        for key, item in net2.items():
            if key.startswith("_empty"):
                net[key] = copy.copy(item)
        reset_results(net)

    for attribute in ['tp_st_percent', 'tp_pos', 'tp_mid', 'tp_min', 'tp_max']:
        if net.trafo[attribute].dtype == 'O':
            net.trafo[attribute] = pd.to_numeric(net.trafo[attribute])
    net["gen"] = net["gen"].rename(columns={"u_pu": "vm_pu"})
    for element, old, new in [("trafo", "unh_kv", "vn_hv_kv"),
                              ("trafo", "unl_kv", "vn_lv_kv"),
                              ("trafo", "uk_percent", "vsc_percent"),
                              ("trafo", "ur_percent", "vscr_percent"),
                              ("trafo3w", "unh_kv", "vn_hv_kv"),
                              ("trafo3w", "unm_kv", "vn_mv_kv"),
                              ("trafo3w", "unl_kv", "vn_lv_kv")]:
        for std_type, parameters in net.std_types[element].items():
            if old in parameters:
                net.std_types[element][std_type][new] = net.std_types[element][std_type].pop(old)
    net.version = 1.0
    if "f_hz" not in net:
        net["f_hz"] = 50.

    if "type" not in net.load.columns:
        net.load["type"] = None
    if "zone" not in net.bus:
        net.bus["zone"] = None
    for element in ["line", "trafo", "bus", "load", "sgen", "ext_grid"]:
        net[element].in_service = net[element].in_service.astype(bool)
    if "in_service" not in net["ward"]:
        net.ward["in_service"] = True
    net.switch.closed = net.switch.closed.astype(bool)


def add_zones_to_elements(net, elements=["line", "trafo", "ext_grid", "switch"]):
    """
    Adds zones to elements, inferring them from the zones of buses they are
    connected to.
    """
    for element in elements:
        if element == "sgen":
            net["sgen"]["zone"] = net["bus"]["zone"].loc[net["sgen"]["bus"]].values
        elif element == "load":
            net["load"]["zone"] = net["bus"]["zone"].loc[net["load"]["bus"]].values
        elif element == "ext_grid":
            net["ext_grid"]["zone"] = net["bus"]["zone"].loc[net["ext_grid"]["bus"]].values
        elif element == "switch":
            net["switch"]["zone"] = net["bus"]["zone"].loc[net["switch"]["bus"]].values
        elif element == "line":
            net["line"]["zone"] = net["bus"]["zone"].loc[net["line"]["from_bus"]].values
            crossing = sum(net["bus"]["zone"].loc[net["line"]["from_bus"]].values !=
                           net["bus"]["zone"].loc[net["line"]["to_bus"]].values)
            if crossing > 0:
                logger.warn("There have been %i lines with different zones at from- and to-bus"
                            % crossing)
        elif element == "trafo":
            net["trafo"]["zone"] = net["bus"]["zone"].loc[net["trafo"]["hv_bus"]].values
            crossing = sum(net["bus"]["zone"].loc[net["trafo"]["hv_bus"]].values !=
                           net["bus"]["zone"].loc[net["trafo"]["lv_bus"]].values)
            if crossing > 0:
                logger.warn("There have been %i trafos with different zones at lv_bus and hv_bus"
                            % crossing)
        elif element == "impedance":
            net["impedance"]["zone"] = net["bus"]["zone"].loc[net["impedance"]["from_bus"]].values
            crossing = sum(net["bus"]["zone"].loc[net["impedance"]["from_bus"]].values !=
                           net["bus"]["zone"].loc[net["impedance"]["to_bus"]].values)
            if crossing > 0:
                logger.warn("There have been %i impedances with different zones at from_bus and "
                            "to_bus" % crossing)
        elif element == "shunt":
            net["shunt"]["zone"] = net["bus"]["zone"].loc[net["shunt"]["bus"]].values
        elif element == "ward":
            net["ward"]["zone"] = net["bus"]["zone"].loc[net["ward"]["bus"]].values
        elif element == "xward":
            net["xward"]["zone"] = net["bus"]["zone"].loc[net["xward"]["bus"]].values
        else:
            raise UserWarning("Unkown element %s" % element)


def create_continuous_bus_index(net, start=0):
    """
    Creates a continuous bus index starting at zero and replaces all
    references of old indices by the new ones.
    """
    new_bus_idxs = list(np.arange(start, len(net.bus) + start))
    bus_lookup = dict(zip(net["bus"].index.values, new_bus_idxs))
    net.bus.index = new_bus_idxs

    for element, value in [("line", "from_bus"), ("line", "to_bus"), ("trafo", "hv_bus"),
                           ("trafo", "lv_bus"), ("sgen", "bus"), ("load", "bus"),
                           ("switch", "bus"), ("ward", "bus"), ("xward", "bus"),
                           ("impedance", "from_bus"), ("impedance", "to_bus"),
                           ("shunt", "bus"), ("ext_grid", "bus")]:
        net[element][value] = get_indices(net[element][value], bus_lookup)
    bb_switches = net.switch[net.switch.et=="b"]
    net.switch.loc[bb_switches.index, "element"] = get_indices(bb_switches.element, bus_lookup)
    return net


def set_scaling_by_type(net, scalings, scale_load=True, scale_sgen=True):
    """
    Sets scaling of loads and/or sgens according to a dictionary
    mapping type to a scaling factor. Note that the type-string is case
    sensitive.
    E.g. scaling = {"pv": 0.8, "bhkw": 0.6}

    :param net:
    :param scalings: A dictionary containing a mapping from element type to
    :param scale_load:
	:param scale_sgen:
    """
    if not isinstance(scalings, dict):
        raise UserWarning("The parameter scaling has to be a dictionary, "
                          "see docstring")

    def scaleit(what):
        et = net[what]
        et["scaling"] = [scale[t] if scale[t] is not None else s for t, s in zip(et.type.values, et.scaling.values)]

    scale = defaultdict(lambda: None, scalings)
    if scale_load:
        scaleit("load")
    if scale_sgen:
        scaleit("sgen")


# --- Modify topology

def close_switch_at_line_with_two_open_switches(net):
    """
    Finds lines that have opened switches at both ends and closes one of them.
    Function is usually used when optimizing section points to
    prevent the algorithm from ignoring isolated lines.
    """
    nl = net.switch[(net.switch.et == 'l') & (net.switch.closed == 0)]
    for i, switch in nl.groupby("element"):
        if len(switch.index) > 1:  # find all lines that have open switches at both ends
            # and close on of them
            net.switch.at[switch.index[0], "closed"] = 1


def drop_inactive_elements(net):
    """
    Drops any elements not in service AND any elements connected to inactive
    buses.
    """
    set_isolated_areas_out_of_service(net)
    # removes inactive lines and its switches and geodata
    inactive_lines = net.line[net.line.in_service == False].index
    drop_lines(net, inactive_lines)

    inactive_trafos = net.trafo[net.trafo.in_service == False].index
    drop_trafos(net, inactive_trafos)

    do_not_delete = set(net.trafo.hv_bus.values) | set(net.trafo.lv_bus.values) | \
                    set(net.line.from_bus.values) | set(net.line.to_bus.values)

    # removes inactive buses safely
    inactive_buses = set(net.bus[net.bus.in_service == False].index) - do_not_delete
    drop_buses(net, inactive_buses)

    for element in net.keys():
        if element not in ["bus", "trafo", "line"] and type(net[element]) == pd.DataFrame \
                and "in_service" in net[element].columns:
            drop_idx = net[element][net[element].in_service == False].index
            net[element].drop(drop_idx, inplace=True)


def drop_buses(net, buses):
    """
    Drops buses and by default safely drops all elements connected to them as well.
    """
    # drop busbus switches
    i = net["switch"][((net["switch"]["element"].isin(buses)) | (net["switch"]["bus"].isin(buses))) \
                      & (net["switch"]["et"] == "b")].index
    net["switch"].drop(i, inplace=True)

    # drop buses and their geodata
    net["bus"].drop(buses, inplace=True)
    net["bus_geodata"].drop(set(buses) & set(net["bus_geodata"].index), inplace=True)


def drop_trafos(net, trafos):
    """
    Deletes all trafos and in the given list of indices and removes
    any switches connected to it.
    """
    # drop any switches
    i = net["switch"].index[(net["switch"]["element"].isin(trafos)) & (net["switch"]["et"] == "t")]
    net["switch"].drop(i, inplace=True)

    # drop the lines+geodata
    net["trafo"].drop(trafos, inplace=True)


def drop_lines(net, lines):
    """
    Deletes all lines and their geodata in the given list of indices and removes
    any switches connected to it.
    """
    # drop any switches
    i = net["switch"][(net["switch"]["element"].isin(lines)) & (net["switch"]["et"] == "l")].index
    net["switch"].drop(i, inplace=True)

    # drop the lines+geodata
    net["line"].drop(lines, inplace=True)
    net["line_geodata"].drop(set(lines) & set(net["line_geodata"].index), inplace=True)


def fuse_buses(net, b1, b2, drop=True):
    """
    Reroutes any connections to buses in b2 to the given bus b1. Additionally drops the buses b2,
    if drop=True (default).
    """
    try:
        b2.__iter__
    except:
        b2 = [b2]

    for element, value in [("line", "from_bus"), ("line", "to_bus"), ("impedance", "from_bus"),
                           ("impedance", "to_bus"), ("trafo", "hv_bus"), ("trafo", "lv_bus"),
                           ("sgen", "bus"), ("load", "bus"),
                           ("switch", "bus"), ("ext_grid", "bus"),
                           ("ward", "bus"), ("xward", "bus"),
                           ("shunt", "bus")]:
        i = net[element][net[element][value].isin(b2)].index
        net[element].loc[i, value] = b1

    i = net["switch"][(net["switch"]["et"] == 'b') & (
        net["switch"]["element"].isin(b2))].index
    net["switch"].loc[i, "element"] = b1
    net["switch"].drop(net["switch"][(net["switch"]["bus"] == net["switch"]["element"]) &
                                     (net["switch"]["et"] == "b")].index, inplace=True)
    if drop:
        net["bus"].drop(b2, inplace=True)
    return net


def set_element_status(net, buses, in_service):
    """
    Sets buses and all elements connected to them in or out of service.
    """
    net.bus.loc[buses, "in_service"] = in_service

    lines = net.line[(net.line.from_bus.isin(buses)) & (net.line.to_bus.isin(buses))].index
    net.line.loc[lines, "in_service"] = in_service

    trafos = net.trafo[(net.trafo.hv_bus.isin(buses)) & (net.trafo.lv_bus.isin(buses))].index
    net.trafo.loc[trafos, "in_service"] = in_service

    impedances = net.impedance[(net.impedance.from_bus.isin(buses)) &
                               (net.impedance.to_bus.isin(buses))].index
    net.impedance.loc[impedances, "in_service"] = in_service

    loads = net.load[net.load.bus.isin(buses)].index
    net.load.loc[loads, "in_service"] = in_service

    sgens = net.sgen[net.sgen.bus.isin(buses)].index
    net.sgen.loc[sgens, "in_service"] = in_service

    wards = net.ward[net.ward.bus.isin(buses)].index
    net.ward.loc[wards, "in_service"] = in_service

    xwards = net.xward[net.xward.bus.isin(buses)].index
    net.xward.loc[xwards, "in_service"] = in_service

    shunts = net.shunt[net.shunt.bus.isin(buses)].index
    net.shunt.loc[shunts, "in_service"] = in_service

    grids = net.ext_grid[net.ext_grid.bus.isin(buses)].index
    net.ext_grid.loc[grids, "in_service"] = in_service


def set_isolated_areas_out_of_service(net):
    """
    Set all isolated buses and all elements connected to isolated buses out of service.
    """
    unsupplied = unsupplied_buses(net)
    set_element_status(net, unsupplied, False)

    for element in ["line", "trafo"]:
        oos_elements = net.line[net.line.in_service == False].index
        oos_switches = net.switch[(net.switch.et == element[0]) &
                                  (net.switch.element.isin(oos_elements))].index
        net.switch.loc[oos_switches, "closed"] = True

        for idx, bus in net.switch[(net.switch.closed == False) & (net.switch.et == element[0])] \
                [["element", "bus"]].values:
            if net.bus.in_service.at[next_bus(net, bus, idx, element)] == False:
                net[element].at[idx, "in_service"] = False


def select_subnet(net, buses, include_switch_buses=False, include_results=False,
                  keep_everything_else=False):
    """
    Selects a subnet by a list of bus indices and returns a net with all elements
    connected to them.
    """
    buses = set(buses)
    if include_switch_buses:
        # we add both buses of a connected line, the one selected is not switch.bus

        # for all line switches
        for _, s in net["switch"].query("et=='l'").iterrows():
            # get from/to-bus of the connected line
            fb = net["line"]["from_bus"].at[s["element"]]
            tb = net["line"]["to_bus"].at[s["element"]]
            # if one bus of the line is selected and its not the switch-bus, add the other bus
            if fb in buses and s["bus"] != fb:
                buses.add(tb)
            if tb in buses and s["bus"] != tb:
                buses.add(fb)

    p2 = create_empty_network()

    p2.bus = net.bus.loc[buses]
    p2.ext_grid = net.ext_grid[net.ext_grid.bus.isin(buses)]
    p2.load = net.load[net.load.bus.isin(buses)]
    p2.sgen = net.sgen[net.sgen.bus.isin(buses)]
    p2.gen = net.gen[net.gen.bus.isin(buses)]
    p2.shunt = net.shunt[net.shunt.bus.isin(buses)]
    p2.ward = net.ward[net.ward.bus.isin(buses)]
    p2.xward = net.xward[net.xward.bus.isin(buses)]

    p2.line = net.line[(net.line.from_bus.isin(buses)) & (net.line.to_bus.isin(buses))]
    p2.trafo = net.trafo[(net.trafo.hv_bus.isin(buses)) & (net.trafo.lv_bus.isin(buses))]
    p2.trafo3w = net.trafo3w[(net.trafo3w.hv_bus.isin(buses)) & (net.trafo3w.mv_bus.isin(buses)) &
                             (net.trafo3w.lv_bus.isin(buses))]
    p2.impedance = net.impedance[(net.impedance.from_bus.isin(buses)) &
                                 (net.impedance.to_bus.isin(buses))]

    if include_results:
        for table in net.keys():
            if net[table] is None:
                continue
            elif table == "res_bus":
                p2[table] = net[table].loc[buses]
            elif table.startswith("res_"):
                p2[table] = net[table].loc[p2[table.split("res_")[1]].index]
    if "bus_geodata" in net:
        p2["bus_geodata"] = net["bus_geodata"].loc[net["bus_geodata"].index.isin(buses)]
    if "line_geodata" in net:
        lines = p2.line.index
        p2["line_geodata"] = net["line_geodata"].loc[net["line_geodata"].index.isin(lines)]

    # switches
    si = [i for i, s in net["switch"].iterrows()
          if s["bus"] in buses and
          ((s["et"] == "b" and s["element"] in p2["bus"].index) or
           (s["et"] == "l" and s["element"] in p2["line"].index) or
           (s["et"] == "t" and s["element"] in p2["trafo"].index))]
    p2["switch"] = net["switch"].loc[si]
    # return a pandapowerNet
    if keep_everything_else:
        newnet = copy.deepcopy(net)
        newnet.update(p2)
        return pandapowerNet(newnet)
    p2["std_types"] = copy.deepcopy(net["std_types"])
    return pandapowerNet(p2)

def merge_nets(net1, net2, validate=True):
    """
    Function to concatenate two nets into one data structure. The second net is reindexed to avoid
    duplicate element indices.
    """
    create_continuous_bus_index(net2, start=net1.bus.index.max() + 1)
    net = copy.deepcopy(net1)
    net2 = copy.deepcopy(net2)
    if validate:
        runpp(net1)
        runpp(net2)

    for element, table in net.items():
        if element.startswith("_") or element.startswith("res"):
            continue
        if type(table) == pd.DataFrame and len(table) > 0:
            if element == "switch":
                bl_switches = net2.switch[net2.switch.et=="l"]
                new_line_index = [net2.line.index.get_loc(ix) + len(net1.line) for ix in bl_switches.element.values]
                net2.switch.loc[bl_switches.index, "element"] = new_line_index

                bl_switches = net1.switch[net1.switch.et=="l"]
                new_line_index = [net1.line.index.get_loc(ix) for ix in bl_switches.element.values]
                net1.switch.loc[bl_switches.index, "element"] = new_line_index

                bt_switches = net2.switch[net2.switch.et=="t"]
                new_trafo_index = [net2.trafo.index.get_loc(ix) + len(net1.trafo) for ix in bt_switches.element.values]
                net2.switch.loc[bt_switches.index, "element"] = new_trafo_index

                bt_switches = net1.switch[net1.switch.et=="t"]
                new_trafo_index = [net1.trafo.index.get_loc(ix) for ix in bt_switches.element.values]
                net1.switch.loc[bt_switches.index, "element"] = new_trafo_index
            net[element] = net1[element].append(net2[element], ignore_index=element!="bus")
    if validate:
        runpp(net)
        dev1 = max(abs(net.res_bus.loc[net1.bus.index].vm_pu.values - net1.res_bus.vm_pu.values))
        dev2 = max(abs(net.res_bus.iloc[len(net1.bus.index):].vm_pu.values - net2.res_bus.vm_pu.values))
        if dev1 > 1e-10 or dev2 > 1e-10:
            raise UserWarning("Deviation in bus voltages after merging")
    return net


# --- item/element selections

def get_element_index(net, element, name, exact_match=True):
    """
    Returns the element(s) identified by a name or regex and its element-table.

    INPUT:
      **net** - pandapower network

      **element** - Table to get indices from ("line", "bus", "trafo" etc.)

      **name** - Name of the element to match.

    OPTIONAL:
      **exact_match** (boolean, True) - True: Expects exactly one match, raises
                                                UserWarning otherwise.
                                        False: returns all indices matching the name/pattern

    OUTPUT:
      **index** - The indices of matching element(s).
    """
    if exact_match:
        idx = net[element][net[element]["name"] == name].index
        if len(idx) == 0:
            raise UserWarning("There is no %s with name %s" % (element, name))
        if len(idx) > 1:
            raise UserWarning("Duplicate %s names for %s" % (element, name))
        return idx[0]
    else:
        return net[element][net[element]["name"].str.match(name, as_indexer=True)].index


def next_bus(net, bus, element_id, et='line', **kwargs):
    """
    Returns the index of the second bus an element is connected to, given a
    first one. E.g. the from_bus given the to_bus of a line.
    """
    # for legacy compliance
    if "element_type" in kwargs:
        et = kwargs["element_type"]

    if et == 'line':
        bc = ["from_bus", "to_bus"]
    elif et == 'trafo':
        bc = ["hv_bus", "lv_bus"]
    elif et == "switch":
        bc = ["bus", "element"]
    else:
        raise Exception("unknown element type")
    nb = list(net[et].loc[element_id, bc].values)
    nb.remove(bus)
    return nb[0]


def get_connected_elements(net, element, buses, respect_switches=True, respect_in_service=False):
    """
     Returns elements connected to a given bus.

     INPUT:
        **net** (pandapowerNet)

        **element** (string, name of the element table)

        **buses** (single integer or iterable of ints)

     OPTIONAL:
        **respect_switches** (boolean, True)    - True: open switches will be respected
                                                  False: open switches will be ignored
        **respect_in_service** (boolean, False) - True: in_service status of connected lines will be
                                                        respected
                                                  False: in_service status will be ignored
     OUTPUT:
        **connected_elements** (set) - Returns connected elements.

    """

    if not hasattr(buses, "__iter__"):
        buses = [buses]

    if element in ["line", "l"]:
        element = "l"
        element_table = net.line
        connected_elements = set(net.line.index[net.line.from_bus.isin(buses)
                                                | net.line.to_bus.isin(buses)])

    elif element in ["trafo"]:
        element = "t"
        element_table = net.trafo
        connected_elements = set(net["trafo"].index[(net.trafo.hv_bus.isin(buses))
                                                    | (net.trafo.lv_bus.isin(buses))])
    elif element in ["trafo3w", "t3w"]:
        element = "t3w"
        element_table = net.trafo3w
        connected_elements = set(net["trafo3w"].index[(net.trafo3w.hv_bus.isin(buses))
                                                      | (net.trafo3w.mv_bus.isin(buses))
                                                      | (net.trafo3w.lv_bus.isin(buses))])
    elif element == "impedance":
        element_table = net.impedance
        connected_elements = set(net["impedance"].index[(net.impedance.from_bus.isin(buses))
                                                        | (net.impedance.to_bus.isin(buses))])
    elif element == "load":
        element_table = net.load
        connected_elements = set(element_table.index[(element_table.bus.isin(buses))])
    elif element == "sgen":
        element_table = net.sgen
        connected_elements = set(element_table.index[(element_table.bus.isin(buses))])
    elif element == "ward":
        element_table = net.ward
        connected_elements = set(element_table.index[(element_table.bus.isin(buses))])
    elif element == "shunt":
        element_table = net.shunt
        connected_elements = set(element_table.index[(element_table.bus.isin(buses))])
    elif element == "xward":
        element_table = net.xward
        connected_elements = set(element_table.index[(element_table.bus.isin(buses))])
    elif element == "ext_grid":
        element_table = net.ext_grid
        connected_elements = set(element_table.index[(element_table.bus.isin(buses))])
    elif element == "gen":
        element_table = net.gen
        connected_elements = set(element_table.index[(element_table.bus.isin(buses))])
    else:
        raise UserWarning("Unknown element! ", element)

    if respect_switches and element in ["l", "t", "t3w"]:
        open_switches = get_connected_switches(net, buses, consider=element, status="open")
        if open_switches:
            open_and_connected = net.switch.loc[net.switch.index.isin(open_switches)
                                                & net.switch.element.isin(connected_elements)].index
            connected_elements -= set(net.switch.element[open_and_connected])

    if respect_in_service:
        connected_elements -= set(element_table[element_table.in_service == False].index)

    return connected_elements


def get_connected_buses(net, buses, consider=("l", "s", "t"), respect_switches=True, respect_in_service=False):
    """
     Returns buses connected to given buses. The source buses will NOT be returned.

     INPUT:
        **net** (pandapowerNet)

        **buses** (single integer or iterable of ints)

     OPTIONAL:
        **respect_switches** (boolean, True)        - True: open switches will be respected
                                                      False: open switches will be ignored
        **respect_in_service** (boolean, False)     - True: in_service status of connected buses
                                                            will be respected
                                                            False: in_service status will be
                                                            ignored
        **consider** (iterable, ("l", "s", "t"))    - Determines, which types of connections will
                                                      be will be considered.
                                                      l: lines
                                                      s: switches
                                                      t: trafos
     OUTPUT:
        **cl** (set) - Returns connected buses.

    """
    if not hasattr(buses, "__iter__"):
        buses = [buses]

    cb = set()
    if "l" in consider:
        cl = get_connected_elements(net, "line", buses, respect_switches, respect_in_service)
        cb |= set(net.line[net.line.index.isin(cl)].from_bus)
        cb |= set(net.line[net.line.index.isin(cl)].to_bus)

    if "s" in consider:
        cs = get_connected_switches(net, buses, consider='b',
                                    status="closed" if respect_switches else "all")
        cb |= set(net.switch[net.switch.index.isin(cs)].element)
        cb |= set(net.switch[net.switch.index.isin(cs)].bus)

    if "t" in consider:
        ct = get_connected_elements(net, "trafo", buses, respect_switches, respect_in_service)
        cb |= set(net.trafo[net.trafo.index.isin(ct)].lv_bus)
        cb |= set(net.trafo[net.trafo.index.isin(cl)].hv_bus)

    if respect_in_service:
        cb -= set(net.bus[~net.bus.in_service].index)

    return cb - set(buses)


def get_connected_buses_at_element(net, element, et, respect_in_service=False):
    """
     Returns buses connected to a given line, switch or trafo. In case of a bus switch, two buses
     will be returned, else one.

     INPUT:
        **net** (pandapowerNet)

        **element** (integer)

        **et** (string)                             - Type of the source element:
                                                      l: line
                                                      s: switch
                                                      t: trafo

     OPTIONAL:
        **respect_in_service** (boolean, False)     - True: in_service status of connected buses
                                                            will be respected
                                                      False: in_service status will be ignored
     OUTPUT:
        **cl** (set) - Returns connected switches.

    """

    cb = set()
    if et == 'l':
        cb.add(net.line.from_bus.at[element])
        cb.add(net.line.to_bus.at[element])

    elif et == 's':
        cb.add(net.switch.bus.at[element])
        if net.switch.et.at[element] == 'b':
            cb.add(net.switch.element.at[element])
    elif et == 't':
        cb.add(net.trafo.hv_bus.at[element])
        cb.add(net.trafo.lv_bus.at[element])

    if respect_in_service:
        cb -= set(net.bus[~net.bus.in_service].index)

    return cb


def get_connected_switches(net, buses, consider=('b', 'l', 't'), status="all"):
    """
    Returns switches connected to given buses.

    INPUT:
        **net** (pandapowerNet)

        **buses** (single integer or iterable of ints)

    OPTIONAL:
        **respect_switches** (boolean, True)        - True: open switches will be respected
                                                     False: open switches will be ignored

        **respect_in_service** (boolean, False)     - True: in_service status of connected
                                                            buses will be respected

                                                      False: in_service status will be ignored
        **consider** (iterable, ("l", "s", "t"))    - Determines, which types of connections
                                                      will be will be considered.
                                                      l: lines
                                                      s: switches
                                                      t: trafos

        **status** (string, ("all", "closed", "open"))    - Determines, which switches will
                                                            be considered
    OUTPUT:
       **cl** (set) - Returns connected buses.

    """

    if not hasattr(buses, "__iter__"):
        buses = [buses]

    if status == "closed":
        switch_selection = net.switch.closed == True
    elif status == "open":
        switch_selection = net.switch.closed == False
    elif status == "all":
        switch_selection = np.full(len(net.switch), True, dtype=bool)
    else:
        logger.warn("Unknown switch status \"%s\" selected! "
                    "Selecting all switches by default." % status)

    cs = set()
    if 'b' in consider:
        cs |= set(net['switch'].index[(net['switch']['bus'].isin(buses)
                                       | net['switch']['element'].isin(buses))
                                      & (net['switch']['et'] == 'b')
                                      & switch_selection])
    if 'l' in consider:
        cs |= set(net['switch'].index[(net['switch']['bus'].isin(buses))
                                      & (net['switch']['et'] == 'l')
                                      & switch_selection])

    if 't' in consider:
        cs |= set(net['switch'].index[net['switch']['bus'].isin(buses)
                                      & (net['switch']['et'] == 't')
                                      & switch_selection])

    return cs

def pq_from_cosphi(s, cosphi, qmode, pmode):
    """
    Calculates P/Q values from rated apparent power and cosine(phi) values.

       - s: rated apparent power
       - cosphi: cosine phi of the
       - qmode: "ind" for inductive or "cap" for capacitive behaviour
       - pmode: "load" for load or "gen" for generation

    As all other pandapower functions this function is based on the consumer viewpoint. For active
    power, that means that loads are positive and generation is negative. For reactive power,
    inductive behaviour is modeled with positive values, capacitive behaviour with negative values.
    """
    if qmode == "ind":
        qsign = 1
    elif qmode == "cap":
        qsign = -1
    else:
        raise ValueError("Unknown mode %s - specify 'ind' or 'cap'"%qmode)

    if pmode == "load":
        psign = 1
    elif pmode == "gen":
        psign = -1
    else:
        raise ValueError("Unknown mode %s - specify 'load' or 'gen'"%pmode)

    p = psign * s * cosphi
    q = qsign * np.sqrt(s**2 - p**2)
    return p, q
