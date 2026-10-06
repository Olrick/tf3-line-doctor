"""Offline tests: run the real mod Lua code against a mocked game api, then the analyzer.

    pip install lupa
    python -m unittest discover -s tests -v
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD = ROOT / "mod" / "olrick_line_doctor_1" / "content" / "line_doctor"
sys.path.insert(0, str(ROOT / "analyzer"))

import analyze  # noqa: E402

try:
    from lupa import LuaRuntime
except ImportError:  # pragma: no cover
    LuaRuntime = None


def lua_path(p: Path) -> str:
    return str(p).replace("\\", "/")


def make_runtime():
    lua = LuaRuntime(unpack_returned_tuples=True)
    # emulate the game's ug_require for this mod's own files
    lua.execute(f"""
        local modDir = "{lua_path(MOD)}/"
        local cache = {{}}
        function ug_require(name)
            local file = name:match("::/line_doctor/(.+)$")
            assert(file, "unexpected ug_require " .. name)
            if not cache[file] then cache[file] = dofile(modDir .. file) end
            return cache[file]
        end
        logLines = {{}}
        function debugPrint(msg) logLines[#logLines + 1] = msg end
        api = dofile("{lua_path(ROOT / 'tests' / 'mock_api.lua')}")
    """)
    return lua


@unittest.skipIf(LuaRuntime is None, "lupa not installed (pip install lupa)")
class LuaModTest(unittest.TestCase):
    def setUp(self):
        self.lua = make_runtime()

    def collect(self) -> dict:
        encoded = self.lua.eval("""(function()
            local collector = ug_require("olrick_line_doctor_1::/line_doctor/collector.lua")
            local json = ug_require("olrick_line_doctor_1::/line_doctor/json.lua")
            return json.encode(collector.collect(api), "  ")
        end)()""")
        return json.loads(encoded)

    def test_json_encoder_edge_cases(self):
        out = self.lua.eval("""(function()
            local json = ug_require("olrick_line_doctor_1::/line_doctor/json.lua")
            return json.encode({ a = { 1, 2, 3 }, b = {}, c = 'q"\\n', d = 0/0, e = 1.5, [3] = true })
        end)()""")
        self.assertEqual(json.loads(out), {"3": True, "a": [1, 2, 3], "b": [], "c": 'q"\n', "d": None, "e": 1.5})

    def test_collect_snapshot(self):
        snap = self.collect()
        self.assertEqual(snap["schemaVersion"], 2)
        self.assertEqual([l["name"] for l in snap["lines"]], ["Bus 1", "Coal A"])
        bus = snap["lines"][0]
        self.assertEqual(len(bus["vehicles"]), 4)
        self.assertEqual(bus["stops"][0]["stationName"], "Gare")
        self.assertEqual(bus["intervalSec"], 900)
        self.assertEqual(bus["finance"]["last12Months"]["income"], 30000)
        self.assertEqual(bus["cargo"]["0"]["waitingPerStop"], [2, 1])
        self.assertAlmostEqual(bus["vehicles"][0]["parts"][0]["ageYears"], 30)
        self.assertEqual(bus["vehicles"][0]["state"], "EN_ROUTE")
        self.assertEqual(bus["vehicles"][0]["loaded"], 5)
        self.assertEqual(list(bus["cargo"]), ["0"])  # only cargo types with capacity, keyed by real type id
        # only the two expected failures (no out-of-range cargo ids)
        self.assertEqual(len(snap["errors"]), 2, snap["errors"])
        self.assertEqual(snap["lines"][1]["vehicles"][0]["capacities"], {"5": 20})
        self.assertEqual(snap["lines"][1]["vehicles"][0]["pendingIncome"], 1234)
        self.assertEqual(bus["vehicles"][0]["finance12m"], {"net": -7500, "income": 7500, "costs": -15000})
        self.assertEqual(snap["cargoNames"], {"0": "Passengers", "5": "Coal"})
        # the failing api call is captured, not fatal
        self.assertTrue(any("getBlockedTrains" in e for e in snap["errors"]))
        self.assertIsNone(snap["network"].get("blockedTrains"))

    def test_game_script_exports_on_load_then_monthly(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.lua.execute(f"""
                app = {{ getUserDataFolder = function() return "{lua_path(Path(tmp))}" end }}
                script = dofile("{lua_path(MOD / 'line_doctor.script.lua')}")
                script = data()
                saved = {{}}
                stateObj = {{ get = function() return saved end, set = function(_, v) saved = v end }}
            """)
            self.lua.execute("script.update({}, stateObj, 0)")  # paused: nothing
            self.assertFalse((Path(tmp) / "export.json").exists())
            self.lua.execute("script.update({}, stateObj, 1)")  # first tick after load
            export = Path(tmp) / "export.json"
            self.assertTrue(export.exists())
            self.assertEqual(json.loads(export.read_text(encoding="utf-8"))["exportReason"], "first")
            self.lua.execute("script.update({}, stateObj, 1)")  # same month: no new export
            self.assertEqual(len((Path(tmp) / "history.jsonl").read_text().splitlines()), 1)
            logs = list(self.lua.eval("logLines").values())
            self.assertTrue(any("exported 2 lines" in l for l in logs), logs)

            # The game runs update() on several threads, each with its own Lua state:
            # a fresh state sharing the saved game-script state must not export again.
            saved = self.lua.eval("saved")
            other = make_runtime()
            other.execute(f"""
                app = {{ getUserDataFolder = function() return "{lua_path(Path(tmp))}" end }}
                dofile("{lua_path(MOD / 'line_doctor.script.lua')}")
                script = data()
            """)
            other.globals().saved = other.table_from(dict(saved))
            other.execute("""
                stateObj = { get = function() return saved end, set = function(_, v) saved = v end }
                script.update({}, stateObj, 1)
            """)
            self.assertEqual(len((Path(tmp) / "history.jsonl").read_text().splitlines()), 1)

            # loading the save later (saved real clock is old) triggers a refresh export
            other.execute("saved.lastExportClock = saved.lastExportClock - 3600; script.update({}, stateObj, 1)")
            lines = (Path(tmp) / "history.jsonl").read_text().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(json.loads(lines[1])["exportReason"], "refresh")

    def test_ticket_probe_observes_without_changing_prices(self):
        self.lua.execute(f"""
            app = nil
            dofile("{lua_path(MOD / 'line_doctor.script.lua')}")
            script = data()
            saved = {{}}
            stateObj = {{ get = function() return saved end, set = function(_, v) saved = v end }}
            r1 = script.handleEvent({{}}, stateObj, "", "TransportVehicleSystem", "OnCalcTicketPrice", {{
                {{ vehicleEntity = 21, lineEntity = 200, simEntity = 9001, stockListEntity = -1, basePrice = 1000, distance = 500 }},
                {{ vehicleEntity = 22, lineEntity = 200, simEntity = 9002, stockListEntity = 77, basePrice = 3000, distance = 1500 }},
            }})
            r2 = script.handleEvent({{}}, stateObj, "", "SimEntityAtVehicleSystem", "OnCalcTicketPrice", {{
                {{ vehicleEntity = 11, lineEntity = 100, simEntity = 9100, basePrice = 50, distance = 300 }},
            }})
            r3 = script.handleEvent({{}}, stateObj, "", "SimCargoSystem", "OnToArriveAtDestination",
                {{ entities = {{ {{ 9001, {{ 77, 0 }} }} }} }})
        """)
        for r in ("r1", "r2", "r3"):
            self.assertIsNone(self.lua.eval(r), "a handler returning a value would change ticket prices")
        summary = json.loads(self.lua.eval("""(function()
            local probe = ug_require("olrick_line_doctor_1::/line_doctor/ticket_probe.lua")
            local json = ug_require("olrick_line_doctor_1::/line_doctor/json.lua")
            return json.encode(probe.takeSummary(api, saved, 5 * 1461000))
        end)()"""))
        coal = summary["lines"]["200"]
        self.assertEqual((coal["events"], coal["final"], coal["transfer"], coal["basePriceSum"]), (2, 1, 1, 4000))
        self.assertEqual(coal["name"], "Coal A")
        self.assertIn("journalIncome", coal)
        self.assertEqual(summary["lines"]["100"]["passenger"], 1)
        self.assertEqual(summary["arrivals"]["cargo"], 1)
        self.assertEqual(summary["arrivals"]["sampled"][0]["sim"], 9001)
        self.assertEqual(len(summary["samples"]), 3)
        chain = summary["chains"][0]                       # 9002: delivered after two lines
        self.assertEqual([seg["line"] for seg in chain["segments"]], [100, 200])
        self.assertEqual(chain["segments"][1]["pos"], [300, 400, 0])
        self.assertEqual(chain["unloadPos"], [900, 1200, 10])
        self.assertEqual(chain["basePrice"], 3000)
        # a new period starts, sampled entities still watched
        self.assertEqual(self.lua.eval("saved.probe.samples[1]"), None)

    def test_exports_continue_when_probe_file_is_unknown(self):
        # the game only indexes mod files at application start: a new file is "not found" after a save reload
        self.lua.execute(f"""
            local real = ug_require
            ug_require = function(name)
                if name:find("ticket_probe") then error("module '" .. name .. "' not found") end
                return real(name)
            end
            io = nil
            app = nil
            dofile("{lua_path(MOD / 'line_doctor.script.lua')}")
            script = data()
            saved = {{}}
            stateObj = {{ get = function() return saved end, set = function(_, v) saved = v end }}
            script.handleEvent({{}}, stateObj, "", "TransportVehicleSystem", "OnCalcTicketPrice", {{}})
            script.update({{}}, stateObj, 1)
        """)
        log_text = "\n".join(self.lua.eval("logLines").values())
        snap = analyze.extract_from_log(log_text)
        self.assertIsNotNone(snap)
        self.assertIn("restart", snap["ticketProbe"]["error"])

    def test_log_fallback_is_parsed_by_analyzer(self):
        self.lua.execute(f"""
            io = nil
            app = nil
            script = dofile("{lua_path(MOD / 'line_doctor.script.lua')}")
            script = data()
            saved = {{}}
            script.update({{}}, {{ get = function() return saved end, set = function(_, v) saved = v end }}, 1)
        """)
        log_text = "\n".join("[2026-10-06 - MESSAGE - Sim - Lua ] " + l for l in self.lua.eval("logLines").values())
        snap = analyze.extract_from_log(log_text)
        self.assertIsNotNone(snap)
        self.assertEqual(len(snap["lines"]), 2)

    def test_end_to_end_diagnostics(self):
        result = analyze.analyze(self.collect())
        by_name = {l["metrics"]["name"]: l for l in result["lines"]}
        bus_codes = {f["code"] for f in by_name["Bus 1"]["findings"]}
        coal_codes = {f["code"] for f in by_name["Coal A"]["findings"]}
        self.assertLess(by_name["Bus 1"]["metrics"]["net12m"], 0)
        self.assertIn("OVERCAPACITY", bus_codes)
        self.assertIn("IDLE_VEHICLES", bus_codes)
        self.assertIn("OLD_FLEET", bus_codes)
        self.assertIn("LOW_FREQUENCY", bus_codes)
        self.assertIn("UNDERCAPACITY", coal_codes)
        self.assertEqual(result["summary"]["deficitLines"], 1)
        # deficit line listed first
        self.assertEqual(result["lines"][0]["metrics"]["name"], "Bus 1")
        md = analyze.to_markdown(result)
        self.assertIn("Bus 1", md)


class AnalyzerFixtureTest(unittest.TestCase):
    def test_schema1_upgrade_and_young_line(self):
        monthly = [0] * 9 + [-2000, -4000, 1500]
        line = {"id": 1, "name": "Bus", "stops": [{"stationGroup": 1}, {"stationGroup": 2}],
                "cargo": {"1": {"capacity": 18, "used": 8}, "2": {"capacity": 0, "used": 0}},
                "vehicles": [{"loaded": [3, 0], "parts": [{"ageYears": 0.3}]}] * 3,
                "ratePerYear": 100, "transportedPerYear": 5,
                "finance": {"last12Months": {"net": -4500, "income": 3000, "vehicleRunningCosts": -7500},
                            "previous12Months": {"net": 0}, "monthlyNet": monthly}}
        snap = analyze.upgrade_snapshot({"schemaVersion": 1, "passengerCargoTypeId": 0, "lines": [line]})
        self.assertEqual(list(snap["lines"][0]["cargo"]), ["0"])
        result = analyze.analyze(snap)
        m = result["lines"][0]["metrics"]
        self.assertEqual(m["kind"], "passengers")
        self.assertTrue(m["young"])
        codes = {f["code"]: f for f in result["lines"][0]["findings"]}
        self.assertEqual(codes["OVERCAPACITY"]["severity"], "minor")  # downgraded on a young line
        self.assertNotIn("DECLINING", codes)                           # no previous year to compare with
        self.assertIn("YOUNG_LINE", codes)

    def _cart_line(self, months_active, fleet_age_years=None):
        monthly = [0] * (12 - months_active) + [-5000] * months_active
        return {"id": 9, "name": "Légumes", "stops": [{"stationGroup": 1}, {"stationGroup": 2}],
                "cargo": {"1": {"capacity": 30, "used": 0, "realSectionTimes": [[0, 0], [0, 0]],
                                "driveSectionTimesSec": [602.0, 527.7]}},
                "vehicles": [{"loaded": 0, "parts": [{"ageYears": fleet_age_years or months_active / 12}]}] * 5,
                "finance": {"last12Months": {"net": -5000 * months_active, "income": 0},
                            "previous12Months": {"net": 0}, "monthlyNet": monthly}}

    def test_slow_line_not_flagged_as_stuck(self):
        # 1130 s round trip = ~9.3 calendar months: 2 months without a trip is normal
        result = analyze.analyze({"schemaVersion": 2, "yearTicks": 1461000, "lines": [self._cart_line(2)]})
        codes = {f["code"] for f in result["lines"][0]["findings"]}
        self.assertIn("FIRST_TRIP_PENDING", codes)
        self.assertNotIn("NO_COMPLETED_TRIP", codes)
        self.assertIn("SLOW_CYCLE", codes)
        self.assertNotIn("NO_REVENUE", codes)      # no trip finished yet: nothing could be earned
        self.assertNotIn("COST_STRUCTURE", codes)
        self.assertAlmostEqual(result["lines"][0]["metrics"]["cycleMonths"], 1129.75 / 121.75, places=2)

    def test_line_without_any_trip_for_too_long_is_stuck(self):
        # two years (> 1.5 round trips) without a single measured section
        result = analyze.analyze({"schemaVersion": 2, "yearTicks": 1461000, "lines": [self._cart_line(12, 2.0)]})
        codes = {f["code"] for f in result["lines"][0]["findings"]}
        self.assertIn("NO_COMPLETED_TRIP", codes)

    def test_feeder_line_is_judged_with_its_connections(self):
        def line(i, name, stations, net, monthly_val):
            return {"id": i, "name": name, "stops": [{"stationGroup": g, "stationName": str(g)} for g in stations],
                    "cargo": {"0": {"capacity": 18, "used": 9, "realSectionTimes": [[120, 3], [120, 3]],
                                    "driveSectionTimesSec": [110, 110]}},
                    "vehicles": [{"loaded": 3, "parts": [{"ageYears": 2}]}] * 3,
                    "finance": {"last12Months": {"net": net, "income": 10000, "vehicleRunningCosts": -10000 + net},
                                "previous12Months": {"net": net}, "monthlyNet": [monthly_val] * 12}}
        snap = {"schemaVersion": 2, "passengerCargoTypeId": 0, "yearTicks": 1461000, "lines": [
            line(1, "Navette", [10, 11, 12], -20000, -1500),
            line(2, "Intercité", [10, 20], 55000, 4500),
            line(3, "Isolée", [30, 31], -5000, -400),
        ]}
        result = analyze.analyze(snap)
        by_name = {l["metrics"]["name"]: {f["code"]: f for f in l["findings"]} for l in result["lines"]}
        feeder = by_name["Navette"]["TRANSFER_DEPENDENT"]
        self.assertEqual(feeder["severity"], "major")            # the group as a whole makes money
        self.assertEqual(feeder["evidence"]["groupNet"], 35000)
        self.assertNotIn("TRANSFER_DEPENDENT", by_name["Isolée"])
        self.assertNotIn("TRANSFER_DEPENDENT", by_name["Intercité"])  # profitable: nothing to explain

    def test_condition_label_matches_game(self):
        # observed in game: 0 % = very bad, 62 % = good
        self.assertEqual(analyze.condition_label(0.0), "très mauvais")
        self.assertEqual(analyze.condition_label(0.62), "bon")
        self.assertEqual(analyze.condition_label(1.0), "très bon")

    @staticmethod
    def _truck_line(i, name, stations, cargo="33", queued=0, fleet=6, legs=(1379.0, 414.0), net=-900000):
        stuck = {"state": 1, "speed": 0, "loaded": 22, "engineStopIndex": 1, "daysAtTerminal": 0,
                 "parts": [{"ageYears": 19}]}
        rolling = {"state": 1, "speed": 12, "loaded": 0, "engineStopIndex": 0, "daysAtTerminal": 0,
                   "parts": [{"ageYears": 19}]}
        return {"id": i, "name": name, "stops": [{"stationGroup": g, "stationName": f"S{g}"} for g in stations],
                "cargo": {cargo: {"capacity": 22 * fleet, "used": 22 * queued,
                                  "realSectionTimes": [[legs[0] + 20, 5], [legs[1] + 20, 5]],
                                  "driveSectionTimesSec": list(legs)}},
                "vehicles": [stuck] * queued + [rolling] * (fleet - queued),
                "finance": {"last12Months": {"net": net, "income": 800000, "vehicleRunningCosts": net - 800000},
                            "previous12Months": {"net": net}, "monthlyNet": [net / 12] * 12}}

    def test_queue_and_asymmetric_route(self):
        snap = {"schemaVersion": 2, "yearTicks": 1461000, "lines": [
            self._truck_line(1, "Briques", [1, 2], queued=5, fleet=6),
            self._truck_line(2, "Fluide", [3, 4], queued=1, fleet=6, legs=(400.0, 390.0)),
        ]}
        result = analyze.analyze(snap)
        by_name = {l["metrics"]["name"]: {f["code"]: f for f in l["findings"]} for l in result["lines"]}
        self.assertEqual(by_name["Briques"]["QUEUE"]["severity"], "critical")
        self.assertEqual(by_name["Briques"]["QUEUE"]["evidence"]["stopName"], "S2")
        self.assertEqual(by_name["Briques"]["ASYMMETRIC_ROUTE"]["severity"], "major")
        self.assertNotIn("QUEUE", by_name["Fluide"])
        self.assertNotIn("ASYMMETRIC_ROUTE", by_name["Fluide"])

    def test_recurring_queue_detected_from_history(self):
        def snap(t, queued):
            return {"schemaVersion": 2, "yearTicks": 1461000, "gameTime": t,
                    "lines": [self._truck_line(1, "Briques", [1, 2], queued=queued, fleet=11)]}
        history = [snap(t, q) for t, q in enumerate([9, 0, 8, 1, 0, 9, 2, 1])]
        result = analyze.analyze(history[-1], analyze.same_game_history(history))
        f = {x["code"]: x for x in result["lines"][0]["findings"]}["QUEUE"]
        self.assertIn("récurrente", f["message"])
        self.assertIn("3 des 8", f["message"])

    def test_history_stops_at_another_game(self):
        other = {"schemaVersion": 2, "gameTime": 5, "lines": [{"id": 99}]}
        mine = [{"schemaVersion": 2, "gameTime": t, "lines": [{"id": 1}]} for t in (10, 20)]
        self.assertEqual(len(analyze.same_game_history([other] + mine)), 2)

    def test_common_deficit_stop_needs_same_cargo(self):
        snap = {"schemaVersion": 2, "yearTicks": 1461000, "lines": [
            self._truck_line(1, "Poissons", [1, 2], cargo="6"),
            self._truck_line(2, "Bus", [2, 3], cargo="0"),
            self._truck_line(3, "Poissons bis", [2, 4], cargo="6"),
        ]}
        stops = analyze.analyze(snap)["commonDeficitStops"]
        self.assertEqual(len(stops), 1)
        self.assertEqual(sorted(stops[0]["deficitLines"]), ["Poissons", "Poissons bis"])

    def test_sample_fixture(self):
        snap = analyze.load_snapshot(str(ROOT / "tests" / "fixtures" / "sample_export.json"))
        result = analyze.analyze(snap)
        self.assertEqual(result["summary"]["lines"], len(snap["lines"]))
        for line in result["lines"]:
            for f in line["findings"]:
                self.assertIn(f["severity"], analyze.SEVERITY_ORDER)


if __name__ == "__main__":
    unittest.main()
