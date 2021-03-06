# Copyright 2014: Mirantis Inc.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import copy
import json
import os

import mako.template

from rally.benchmark.processing.charts import histogram as histo
from rally.benchmark.processing import utils


def _prepare_data(data, reduce_rows=1000):
    durations = []
    idle_durations = []
    atomic_durations = {}
    num_errors = 0

    for i in data["result"]:
        # TODO(maretskiy): store error value and scenario output

        if i["error"]:
            num_errors += 1

        durations.append(i["duration"])
        idle_durations.append(i["idle_duration"])

        for met, duration in i["atomic_actions"].items():
            try:
                atomic_durations[met].append(duration)
            except KeyError:
                atomic_durations[met] = [duration]

    for k, v in atomic_durations.items():
        atomic_durations[k] = utils.compress(v, limit=reduce_rows)

    return {
        "total_durations": {
            "duration": utils.compress(durations, limit=reduce_rows),
            "idle_duration": utils.compress(idle_durations,
                                            limit=reduce_rows)},
        "atomic_durations": atomic_durations,
        "num_errors": num_errors,
    }


def _process_main_duration(result, data):
    histogram_data = [r["duration"] for r in result["result"]
                      if not r["error"]]
    histograms = []
    if histogram_data:
        hvariety = histo.hvariety(histogram_data)
        for i in range(len(hvariety)):
            histograms.append(histo.Histogram(histogram_data,
                                              hvariety[i]['number_of_bins'],
                                              hvariety[i]['method']))

    stacked_area = []
    for key in "duration", "idle_duration":
        stacked_area.append({
            "key": key,
            "values": [(i, round(d, 2))
                       for i, d in data["total_durations"][key]],
        })

    return {
        "pie": [
            {"key": "success", "value": len(histogram_data)},
            {"key": "errors", "value": data["num_errors"]},
        ],
        "iter": stacked_area,
        "histogram": [
            {
                "key": "task",
                "method": histogram.method,
                "values": [{"x": round(x, 2), "y": float(y)}
                           for x, y in zip(histogram.x_axis, histogram.y_axis)]
            } for histogram in histograms
        ],
    }


def _process_atomic(result, data):

    def avg(lst, key=None):
        lst = lst if not key else map(lambda x: x[key], lst)
        return utils.mean(lst)

    # NOTE(boris-42): In our result["result"] we have next structure:
    #                 {"error": NoneOrDict,
    #                  "atomic_actions": {
    #                       "action1": <duration>,
    #                       "action2": <duration>
    #                   }
    #                 }
    #                 Our goal is to get next structure:
    #                 [{"key": $atomic_actions.action,
    #                   "values": [[order, $atomic_actions.duration
    #                              if not $error else 0], ...}]
    #
    #                 Order of actions in "atomic_action" is similiar for
    #                 all iteration. So we should take first non "error"
    #                 iteration. And get in atomitc_iter list:
    #                 [{"key": "action", "values":[]}]
    stacked_area = []
    for row in result["result"]:
        if not row["error"] and "atomic_actions" in row:
            stacked_area = [{"key": a, "values": []}
                            for a in row["atomic_actions"]]
            break

    # NOTE(boris-42): pie is similiar to stacked_area, only difference is in
    #                 structure of values. In case of $error we shouldn't put
    #                 anything in pie. In case of non error we should put just
    #                 $atomic_actions.duration (without order)
    pie = []
    histogram_data = []
    if stacked_area:
        pie = copy.deepcopy(stacked_area)
        histogram_data = copy.deepcopy(stacked_area)
        for i, res in enumerate(result["result"]):
            # in case of error put (order, 0.0) to all actions of stacked area
            if res["error"]:
                for k in range(len(stacked_area)):
                    stacked_area[k]["values"].append([i + 1, 0.0])
                continue

            # in case of non error put real durations to pie and stacked area
            for j, action in enumerate(res["atomic_actions"].keys()):
                # in case any single atomic action failed, put 0
                action_duration = res["atomic_actions"][action] or 0.0
                pie[j]["values"].append(action_duration)
                histogram_data[j]["values"].append(action_duration)

    # filter out empty action lists in pie / histogram to avoid errors
    pie = filter(lambda x: x["values"], pie)
    histogram_data = filter(lambda x: x["values"], histogram_data)

    histograms = [[] for atomic_action in range(len(histogram_data))]
    for i, atomic_action in enumerate(histogram_data):
        hvariety = histo.hvariety(atomic_action['values'])
        for v in range(len(hvariety)):
            histograms[i].append(histo.Histogram(atomic_action['values'],
                                                 hvariety[v]['number_of_bins'],
                                                 hvariety[v]['method'],
                                                 atomic_action['key']))
    stacked_area = []
    for name, durations in data["atomic_durations"].iteritems():
        stacked_area.append({
            "key": name,
            "values": [(i, round(d, 2)) for i, d in durations],
        })

    return {
        "histogram": [[
            {
                "key": action.key,
                "disabled": i,
                "method": action.method,
                "values": [{"x": round(x, 2), "y": y}
                           for x, y in zip(action.x_axis, action.y_axis)]
            } for action in atomic_action_list]
            for i, atomic_action_list in enumerate(histograms)
        ],
        "iter": stacked_area,
        "pie": map(lambda x: {"key": x["key"], "value": avg(x["values"])}, pie)
    }


def _get_atomic_action_durations(result):
    raw = result.get('result', [])
    actions_data = utils.get_atomic_actions_data(raw)
    table = []
    total = []
    for action in actions_data:
        durations = actions_data[action]
        if durations:
            data = [action,
                    round(min(durations), 3),
                    round(utils.mean(durations), 3),
                    round(max(durations), 3),
                    round(utils.percentile(durations, 0.90), 3),
                    round(utils.percentile(durations, 0.95), 3),
                    "%.1f%%" % (len(durations) * 100.0 / len(raw)),
                    len(raw)]
        else:
            data = [action, None, None, None, None, None, 0, len(raw)]

        # Save `total' - it must be appended last
        if action == "total":
            total = data
            continue
        table.append(data)

    if total:
        table.append(total)

    return table


def _process_results(results):
    output = []
    for result in results:
        table_cols = ["action",
                      "min (sec)",
                      "avg (sec)",
                      "max (sec)",
                      "90 percentile",
                      "95 percentile",
                      "success",
                      "count"]
        table_rows = _get_atomic_action_durations(result)
        name, kw, pos = (result["key"]["name"],
                         result["key"]["kw"], result["key"]["pos"])
        data = _prepare_data(result)
        cls = name.split(".")[0]
        met = name.split(".")[1]

        output.append({
            "cls": cls,
            "met": met,
            "pos": int(pos),
            "name": "%s%s" % (met, (pos and " [%d]" % (int(pos) + 1) or "")),
            "config": json.dumps({name: kw}, indent=2),
            "duration": _process_main_duration(result, data),
            "atomic": _process_atomic(result, data),
            "table_cols": table_cols,
            "table_rows": table_rows,
        })
    return sorted(output, key=lambda r: "%s%s" % (r["cls"], r["name"]))


def plot(results):
    data = _process_results(results)

    template_file = os.path.join(os.path.dirname(__file__),
                                 "src", "index.mako")
    with open(template_file) as index:
        template = mako.template.Template(index.read())
        return template.render(data=json.dumps(data))
