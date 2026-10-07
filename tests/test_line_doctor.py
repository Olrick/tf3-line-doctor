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
import chains  # noqa: E402
import infra as infra_mod  # noqa: E402
import archive  # noqa: E402
import history  # noqa: E402

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

    def test_every_mod_lua_file_compiles(self):
        # the GUI script cannot run outside the game, but a syntax error would break the whole mod
        mod_root = MOD.parent
        files = sorted(mod_root.rglob("*.lua"))
        listed = json.loads((mod_root.parent / "_content.json").read_text())["files"]
        self.assertEqual(len(files), len(listed))  # every listed file is checked
        for f in files:
            ok, err = self.lua.eval("function(src, name) local fn, e = load(src, name) return fn ~= nil, e end")(
                f.read_text(encoding="utf-8"), f.name)
            self.assertTrue(ok, f"{f.name}: {err}")

    def test_content_list_matches_files(self):
        listed = set(json.loads((MOD.parent.parent / "_content.json").read_text())["files"])
        actual = {"line_doctor/" + f.name for f in MOD.iterdir()}
        self.assertEqual(listed, actual)

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
            other.globals().saved = other.table_from(
                {k: saved[k] for k in ("lastExport", "lastExportClock")})  # nested tables cannot cross runtimes
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

    def test_pending_income_shares_by_segment_length(self):
        # sim 9002: picked up at (0,0,0) by line 100, transferred at (300,400,0) to line 200,
        # now in vehicle 22 at (900,1200,10), heading to construction 77 at (1500,2000,10)
        res = json.loads(self.lua.eval("""(function()
            local pending = ug_require("olrick_line_doctor_1::/line_doctor/pending.lua")
            local json = ug_require("olrick_line_doctor_1::/line_doctor/json.lua")
            return json.encode(pending.compute(api, {}, 0))
        end)()"""))
        import math
        seg1 = 500.0
        seg2 = math.sqrt(600**2 + 800**2 + 10**2) + 80          # climb of 10 m counts 8x
        rest = 1000.0
        price = 3.9 * (math.sqrt(1500**2 + 2000**2 + 10**2) + 80) * 0.804
        total = seg1 + seg2 + rest
        self.assertEqual(res["items"], 1)
        self.assertAlmostEqual(res["lines"]["100"]["done"], price * seg1 / total, delta=0.01)  # json keeps 6 digits
        self.assertAlmostEqual(res["lines"]["200"]["inProgress"], price * seg2 / total, delta=0.01)
        self.assertEqual(res["lines"]["200"]["units"], 1)
        self.assertEqual(res["lines"]["100"]["units"], 0)

    def test_pending_groups_items_by_partial_chain(self):
        res = json.loads(self.lua.eval("""(function()
            local pending = ug_require("olrick_line_doctor_1::/line_doctor/pending.lua")
            local json = ug_require("olrick_line_doctor_1::/line_doctor/json.lua")
            local s = {}
            pending.learnDelivery(s, { cargoType = 5, basePrice = 9000, t = 10,
                segments = { { line = 100, pos = { 0, 0, 0 } }, { line = 200 }, { line = 300 } }, unloadPos = { 9, 9, 0 } })
            return json.encode(pending.compute(api, s, 20))
        end)()"""))
        self.assertEqual(res["chains"]["5|100>200>300"]["n"], 1)          # delivered chain remembered
        (prefix,) = res["prefixes"]                                        # sim 9002: lines 100 then 200, in vehicle
        self.assertEqual(prefix["lines"], [100, 200])
        self.assertTrue(prefix["inVehicle"])
        self.assertEqual(prefix["units"], 1)
        self.assertAlmostEqual(prefix["done"][0], res["lines"]["100"]["done"], places=2)
        self.assertAlmostEqual(prefix["inProgress"], res["lines"]["200"]["inProgress"], places=2)

    def test_pending_positions_from_observed_deliveries_and_stops(self):
        res = json.loads(self.lua.eval("""(function()
            local pending = ug_require("olrick_line_doctor_1::/line_doctor/pending.lua")
            local json = ug_require("olrick_line_doctor_1::/line_doctor/json.lua")
            local s = {}
            local first = pending.compute(api, s, 10)          -- customer 88 never delivered yet: skipped
            pending.learnDelivery(s, { cargoType = 5, basePrice = 9000, t = 11, target = 88,
                segments = { { line = 100, pos = { 0, 0, 0 } } }, unloadPos = { 300, 1400, 0 } })
            local second = pending.compute(api, s, 20)
            return json.encode({ first = first, second = second, stop = s.stopPos["200:0"] })
        end)()"""))
        self.assertEqual(res["first"]["skippedWhy"]["noTarget"], 1)
        self.assertEqual(res["stop"], [300, 400, 0])           # learned from vehicle 21 standing there
        self.assertEqual(res["second"]["items"], 2)            # 9002 in a vehicle, 9003 waiting at the stop
        self.assertEqual(res["second"]["skipped"], 0)
        # 9003: line 100 drove 500 m of a 500 + 1000 m trip, all done (waiting for line 200)
        self.assertGreater(res["second"]["lines"]["100"]["done"], 0)

    def test_pending_learns_factor_from_deliveries(self):
        f = self.lua.eval("""(function()
            local pending = ug_require("olrick_line_doctor_1::/line_doctor/pending.lua")
            local s = {}
            pending.learnDelivery(s, { cargoType = 5, basePrice = 4000,
                segments = { { pos = { 0, 0, 0 } } }, unloadPos = { 600, 800, 0 } })
            pending.learnDelivery(s, { cargoType = 5, basePrice = 5000,
                segments = { { pos = { 0, 0, 0 } } }, unloadPos = { 600, 800, 0 } })
            return s.calib.types["5"].v
        end)()""")
        self.assertAlmostEqual(f, 4.0 + 0.1 * (5.0 - 4.0))  # first value, then moving average

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

    def test_company_finance_table(self):
        fs = analyze.finance_summary(self.collect())
        self.assertEqual(fs["headers"], ["1981", "1982"])
        self.assertEqual(fs["rows"]["Recettes de transport (road)"], [40000, 42000])
        self.assertEqual(fs["rows"]["Entretien de l'infrastructure (road)"], [-25000, -27000])
        self.assertEqual(fs["rows"]["Constructions : track"], [0, -2000])
        self.assertIn("Entretien de l'infrastructure", analyze.finance_markdown(fs))

    def test_infrastructure_upkeep_by_building(self):
        infra = self.collect()["infrastructure"]
        self.assertEqual(infra["total"], 80000)              # the two stations (streets/tracks: finance table)
        self.assertEqual([b["id"] for b in infra["buildings"]], [602, 601])          # most expensive first
        truck, bus = infra["buildings"]
        self.assertEqual((truck["kind"], truck["lines"]), ("gare de marchandises (route)", []))
        self.assertEqual((bus["kind"], bus["lines"], bus["name"]), ("arrêt / gare routière", [100], "Gare"))
        self.assertEqual(infra["via"], {"stations": 2})
        self.assertEqual(bus["costParts"]["building"], 30000)
        # report: costs scaled on the finance table's infrastructure upkeep (mock: 25 000 / period)
        snap = self.collect()
        report = infra_mod.build(snap)
        self.assertAlmostEqual(report["financeUpkeep"], 25000)
        self.assertEqual(sum(b["cost"] for b in report["buildings"]), 80000)  # mock: component costs only
        self.assertEqual(report["kinds"]["gare de marchandises (route)"]["unused"], 1)
        unused = next(b for b in report["buildings"] if not b["lines"])
        self.assertEqual(unused["nearest"]["name"], "Gare")             # the served bus station
        self.assertAlmostEqual(unused["nearest"]["distance"], 50)       # 30 / 40 / 50 m
        self.assertAlmostEqual(unused["nearest"]["dz"], -8)             # 8 m lower: underground level
        self.assertIn("sans aucune ligne", infra_mod.render(report))

    def test_archive_keeps_one_file_per_export_per_game(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "stdout.txt"
            def block(snap):
                text = json.dumps(snap)
                return (f"x [LineDoctor] LINE_DOCTOR_BEGIN {len(text)}\n"
                        f"x [LineDoctor] LINE_DOCTOR|{text}\n"
                        "x [LineDoctor] LINE_DOCTOR_END\n")
            snaps = [{"gameId": "A", "gameTime": t, "lines": []} for t in (100, 200)] + [{"gameId": "B", "gameTime": 50, "lines": []}]
            log.write_text("".join(block(x) for x in snaps), encoding="utf-8")
            root = Path(tmp) / "exports"
            folder = archive.sync(str(log), root)
            self.assertEqual(folder.name, "B")                       # game of the latest export
            self.assertEqual(len(archive.load_game(root / "A")), 2)
            archive.sync(str(log), root)                             # idempotent
            self.assertEqual(len(list((root / "A").glob("0*.json"))), 2)

    def test_history_groups_years(self):
        h = history.build({"financeHistory": self.collect()["financeTable"]})
        self.assertEqual(h["headers"], ["1981", "1982"])
        self.assertEqual(h["groups"]["Recettes"], [40000, 42000])
        self.assertEqual(h["groups"]["Entretien des bâtiments"], [-25000, -27000])
        self.assertIn("Historique", history.render(h))

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

    @staticmethod
    def _wood_snapshot():
        def line(i, name, stops, net):
            return {"id": i, "name": name, "stops": [{"stationName": x} for x in stops], "vehicles": [{}],
                    "finance": {"last12Months": {"net": net}}}
        return {
            "date": {"day": 1, "month": 1, "year": 1980}, "yearTicks": 1461000, "cargoNames": {"12": "Bois"},
            "lines": [line(1, "Camion bois", ["Camp", "Gare corresp."], 900000),
                      line(2, "Train bois", ["Gare", "Loos"], 9600000),
                      line(5, "Bois neuf", ["Forêt", "Scierie"], -50000)],
            "pendingIncome": {
                "k": 0.804, "items": 33,
                "chains": {"12|1>2": {"cargoType": 12, "lines": [1, 2], "n": 4, "price": 400000, "first": 0, "last": 1461000,
                                      "targets": {"900": 4}, "stepShare": [0.4, 3.6], "nShare": 4}},
                "targetNames": {"900": "Scierie de Loos"},
                "prefixes": [
                    {"cargoType": 12, "lines": [1], "inVehicle": False, "units": 22, "done": [1000], "inProgress": 0},
                    {"cargoType": 12, "lines": [1, 2], "inVehicle": True, "units": 10, "done": [500], "inProgress": 3000},
                    {"cargoType": 12, "lines": [5], "inVehicle": True, "units": 1, "done": [0], "inProgress": 40},
                ]}}

    def test_chains_rebuild_end_to_end(self):
        built, meta = chains.build(self._wood_snapshot())
        wood = next(c for c in built if c.lines == [1, 2])
        self.assertEqual([s.name for s in wood.steps], ["Camion bois", "Train bois"])
        self.assertEqual(wood.steps[0].done, 1500)          # both partial chains completed the truck step
        self.assertEqual(wood.steps[0].waiting_after, 22)   # waiting at the station for the train
        self.assertEqual(wood.steps[1].in_progress, 3000)
        self.assertEqual(wood.steps[1].on_board, 10)
        self.assertEqual((wood.pending, wood.in_progress, wood.net12m), (1500, 3000, 10500000))
        self.assertEqual(wood.allocated, 10500000)          # lines not shared: full results
        self.assertEqual(wood.potential, 10500000 + 1500 + 3000)
        new = next(c for c in built if c.lines == [5])
        self.assertFalse(new.delivered)
        self.assertEqual(wood.destination, "Scierie de Loos")
        self.assertAlmostEqual(wood.steps[0].price_share, 0.1)
        self.assertAlmostEqual(wood.unit_value, 100000 * 0.804)
        page = chains.render(chains.build_trees(built), meta)
        self.assertIn("Camion bois", page)
        self.assertIn("Scierie de Loos", page)
        self.assertIn("pas encore livrée", page)

    def test_production_tree_from_final_product(self):
        def line(i, name):
            return {"id": i, "name": name, "stops": [{"stationName": name + " A"}, {"stationName": name + " B"}],
                    "vehicles": [{}], "finance": {"last12Months": {"net": 1000 * i}}}
        def chain(cargo, lines, src, dst):
            return {"cargoType": cargo, "lines": lines, "n": 10, "price": 10000, "first": 0, "last": 1,
                    "sources": {src: 10}, "targets": {dst: 10}}
        snap = {"date": {}, "yearTicks": 1461000,
                "cargoNames": {"27": "Vêtements", "26": "Tissu", "25": "Laine", "2": "Colorants"},
                "lines": [line(1, "Vetements"), line(2, "Tissu camions"), line(3, "Ligne 7"),
                          line(4, "Peinture"), line(5, "Train colorant"), line(6, "Camions colorant")],
                "pendingIncome": {"k": 0.804, "prefixes": [],
                    "targetNames": {"10": "Usine textile", "11": "Ville de Vandœuvre", "12": "Tisseranderie",
                                    "13": "Ferme", "14": "Usine chimique"},
                    "chains": {"27|1": chain(27, [1], "10", "11"), "26|2": chain(26, [2], "12", "10"),
                               "25|3": chain(25, [3], "13", "12"), "2|4>5>6": chain(2, [4, 5, 6], "14", "10")}}}
        built, meta = chains.build(snap)
        trees = chains.build_trees(built)
        self.assertEqual(len(trees), 1)                              # one product: clothes to the town
        root = trees[0]
        self.assertEqual(root.chain.cargo, "Vêtements")
        self.assertEqual(sorted(i.chain.cargo for i in root.inputs), ["Colorants", "Tissu"])
        tissu = next(i for i in root.inputs if i.chain.cargo == "Tissu")
        self.assertEqual([i.chain.cargo for i in tissu.inputs], ["Laine"])
        self.assertEqual(root.allocated, 1000 + 2000 + 3000 + 4000 + 5000 + 6000)
        page = chains.render(trees, meta)
        self.assertIn("arbre de 4 chaînes", page)

    def test_sample_fixture(self):
        snap = analyze.load_snapshot(str(ROOT / "tests" / "fixtures" / "sample_export.json"))
        result = analyze.analyze(snap)
        self.assertEqual(result["summary"]["lines"], len(snap["lines"]))
        for line in result["lines"]:
            for f in line["findings"]:
                self.assertIn(f["severity"], analyze.SEVERITY_ORDER)


if __name__ == "__main__":
    unittest.main()
