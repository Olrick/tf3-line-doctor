-- Line Doctor game script (read-only: never sends commands, never books money).
--
-- Exports a JSON snapshot of every player line when either
--   * an in-game month has passed since the last export, or
--   * REFRESH_REAL_SEC real seconds have passed (this also triggers right after loading a save,
--     whose saved clock is old).
-- The decision only uses the saved state: the game runs update() on several simulation threads,
-- each with its own Lua state, so file-level variables are NOT shared between calls.
--
-- Output, first one that works:
--   1. <user data folder>/line_doctor/export.json  (+ history.jsonl, one compact snapshot per line)
--   2. line_doctor_export.json in the game's working directory
--   3. the game log (stdout.txt), between LINE_DOCTOR_BEGIN / LINE_DOCTOR_END markers
-- The analyzer (analyzer/analyze.py) accepts any of the three.
--
-- State saved with the game: { lastExport = <game time>, lastExportClock = <os.time() or nil> }

local collector = ug_require "olrick_line_doctor_1::/line_doctor/collector.lua"
local json = ug_require "olrick_line_doctor_1::/line_doctor/json.lua"
-- optional: a file added to the mod is only known to the game after an application restart
-- (reloading a save is not enough), so a missing probe must not stop the exports
local okProbeModule, probe = pcall(ug_require, "olrick_line_doctor_1::/line_doctor/ticket_probe.lua")
if not okProbeModule then probe = nil end
local okPendingModule, pending = pcall(ug_require, "olrick_line_doctor_1::/line_doctor/pending.lua")
if not okPendingModule then pending = nil end

local LOG_PREFIX = "[LineDoctor] "
local LOG_CHUNK = 3000
local REFRESH_REAL_SEC = 300

local function clock()
	local ok, t = pcall(function() return os and os.time and os.time() end)
	if ok then return t end
	return nil
end

local function log(msg)
	if debugPrint then
		debugPrint(LOG_PREFIX .. msg)
	else
		print(LOG_PREFIX .. msg)
	end
end

local function candidateDirs()
	local dirs = {}
	pcall(function()
		if app and app.getUserDataFolder then
			local base = app.getUserDataFolder()
			if base and base ~= "" then
				if not base:match("[/\\]$") then base = base .. "/" end
				dirs[#dirs + 1] = base .. "line_doctor/"
				dirs[#dirs + 1] = base
			end
		end
	end)
	dirs[#dirs + 1] = ""
	return dirs
end

local function writeFile(path, content, mode)
	local f = io.open(path, mode or "w")
	if not f then return false end
	f:write(content)
	f:close()
	return true
end

local function writeToFile(pretty, compact)
	if io == nil or io.open == nil then return nil end
	for _, dir in ipairs(candidateDirs()) do
		local name = (dir == "") and "line_doctor_export.json" or (dir .. "export.json")
		local ok, written = pcall(writeFile, name, pretty)
		if ok and written then
			local histName = (dir == "") and "line_doctor_history.jsonl" or (dir .. "history.jsonl")
			pcall(writeFile, histName, compact .. "\n", "a")
			return name
		end
	end
	return nil
end

local function writeToLog(compact)
	log("LINE_DOCTOR_BEGIN " .. #compact)
	for i = 1, #compact, LOG_CHUNK do
		log("LINE_DOCTOR|" .. compact:sub(i, i + LOG_CHUNK - 1))
	end
	log("LINE_DOCTOR_END")
end

local function export(reason, ticketProbe, pendingIncome)
	local ok, snapshot = pcall(collector.collect, api)
	if not ok then
		log("collect failed: " .. tostring(snapshot))
		return false
	end
	snapshot.exportReason = reason
	snapshot.ticketProbe = ticketProbe
	snapshot.pendingIncome = pendingIncome

	local pretty = json.encode(snapshot, "  ")
	local compact = json.encode(snapshot)
	local path = writeToFile(pretty, compact)
	if path then
		log(string.format("exported %d lines (%d bytes, %d api errors) to %s",
			#snapshot.lines, #pretty, #snapshot.errors, path))
	else
		writeToLog(compact)
		log(string.format("file output unavailable (io=%s, app=%s), exported %d lines to the game log",
			tostring(io ~= nil), tostring(app ~= nil), #snapshot.lines))
	end
	return true
end

function data()
	return {
		update = function(_userParams, state, dt)
			if dt == 0 then return end

			local okTime, now = pcall(function()
				local gt = api.engine.getComponent(api.engine.util.getWorld(), api.type.ComponentType.GAME_TIME)
				return gt.gameTime
			end)
			if not okTime or now == nil then return end

			-- price and delivery events, observed only (see ticket_probe.lua)
			pcall(function()
				if not state:hasEventSubscriptions() then
					state:subscribeToEvent("OnCalcTicketPrice")
					state:subscribeToEvent("OnToArriveAtDestination")
				end
			end)

			local s = state:get() or {}
			local monthTicks = api.util.getDefaultMonthDuration()

			local realNow = clock()

			local reason
			if s.lastExport == nil then
				reason = "first"
			elseif now < s.lastExport then
				reason = "load" -- an older save was loaded
			elseif now - s.lastExport >= monthTicks then
				reason = "monthly"
			elseif realNow and (s.lastExportClock == nil or realNow - s.lastExportClock >= REFRESH_REAL_SEC
					or realNow < s.lastExportClock) then
				reason = "refresh"
			end
			if reason == nil then return end

			-- record first, so that a failing export is not retried on every tick
			s.lastExport = now
			s.lastExportClock = realNow
			local ticketProbe
			if probe then
				local okProbe, summary = pcall(probe.takeSummary, api, s, now)
				if not okProbe then ticketProbe = { error = tostring(summary) } else ticketProbe = summary end
				if okProbe and summary and summary.passengerRatio and pending then
					pcall(pending.learnRatio, s, summary.passengerRatio.journal, summary.passengerRatio.price)
				end
			else
				ticketProbe = { error = "ticket_probe.lua not loaded: restart the game application" }
			end

			local pendingIncome
			if pending then
				local t0 = clock()
				local okPending, result = pcall(pending.compute, api, s, now)
				if okPending then
					result.calib = s.calib
					pendingIncome = result
					s.pending = { t = now, lines = result.lines, items = result.items } -- read by the GUI
				else
					pendingIncome = { error = tostring(result) }
				end
				log(string.format("pending income: %s items, %s skipped",
					tostring(okPending and result.items), tostring(okPending and result.skipped)))
			else
				pendingIncome = { error = "pending.lua not loaded: restart the game application" }
			end
			state:set(s)
			export(reason, ticketProbe, pendingIncome)
		end,

		-- never returns a value: ticket prices are observed, not modified
		handleEvent = function(_userParams, state, _src, id, name, param)
			if probe == nil or (name ~= "OnCalcTicketPrice" and name ~= "OnToArriveAtDestination") then return end
			pcall(function()
				local gt = api.engine.getComponent(api.engine.util.getWorld(), api.type.ComponentType.GAME_TIME)
				local s = state:get() or {}
				if name == "OnCalcTicketPrice" then
					local learn = pending and function(detail) pending.learnDelivery(s, detail) end or nil
					probe.onTicketPrice(api, s, id, param, gt.gameTime, learn)
				else
					probe.onArrive(api, s, id, param, gt.gameTime)
				end
				state:set(s)
			end)
		end,
	}
end
