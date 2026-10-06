-- Line Doctor game script (read-only: never sends commands, never books money).
--
-- Exports a JSON snapshot of every player line:
--   * once shortly after a game is loaded,
--   * then every in-game month.
--
-- Output, first one that works:
--   1. <user data folder>/line_doctor/export.json  (+ history.jsonl, one compact snapshot per line)
--   2. line_doctor_export.json in the game's working directory
--   3. the game log (stdout.txt), between LINE_DOCTOR_BEGIN / LINE_DOCTOR_END markers
-- The analyzer (analyzer/analyze.py) accepts any of the three.
--
-- State saved with the game: { lastExport = <game time> }

local collector = ug_require "olrick_line_doctor_1::/line_doctor/collector.lua"
local json = ug_require "olrick_line_doctor_1::/line_doctor/json.lua"

local LOG_PREFIX = "[LineDoctor] "
local LOG_CHUNK = 3000

local exportedThisSession = false

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

local function export(reason)
	local ok, snapshot = pcall(collector.collect, api)
	if not ok then
		log("collect failed: " .. tostring(snapshot))
		return false
	end
	snapshot.exportReason = reason

	local pretty = json.encode(snapshot, "  ")
	local compact = json.encode(snapshot)
	local path = writeToFile(pretty, compact)
	if path then
		log(string.format("exported %d lines (%d bytes, %d api errors) to %s",
			#snapshot.lines, #pretty, #snapshot.errors, path))
	else
		writeToLog(compact)
		log(string.format("file output unavailable, exported %d lines to the game log", #snapshot.lines))
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

			local s = state:get() or {}
			local monthTicks = api.util.getDefaultMonthDuration()

			local reason
			if not exportedThisSession then
				reason = "load"
			elseif s.lastExport == nil or now - s.lastExport >= monthTicks or now < s.lastExport then
				reason = "monthly"
			end
			if reason == nil then return end

			exportedThisSession = true
			export(reason)
			s.lastExport = now
			state:set(s)
		end,

		handleEvent = function(_userParams, _state, _src, _id, _name)
		end,
	}
end
