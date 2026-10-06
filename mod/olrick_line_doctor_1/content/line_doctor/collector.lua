-- Line Doctor: collects a read-only snapshot of every player line.
--
-- The game API is passed in (`collect(api)`) instead of read from the global, so the file can be
-- unit-tested with a mocked api outside the game (see tests/test_lua_mod.py).
-- Every engine call goes through `try`: a call that fails (API changed, entity removed...) is
-- recorded in `snapshot.errors` and the field is left out, the export never aborts.
--
-- Conventions of the exported data:
--   * money in game currency, costs negative (game sign convention)
--   * times in game ticks unless the field name says Sec
--   * stop indices are 1-based in the export (`index`), `engineStopIndex` is what the engine uses

local M = {}

M.SCHEMA_VERSION = 2

local errors

local function try(label, fn, ...)
	local ok, result = pcall(fn, ...)
	if ok then return result end
	if errors and #errors < 200 then
		errors[#errors + 1] = label .. ": " .. tostring(result)
	end
	return nil
end

-- Iterates a native vector / lua array (both support # and [] in the game api).
local function list(v)
	local out = {}
	if v == nil then return out end
	local ok = pcall(function()
		for i = 1, #v do out[#out + 1] = v[i] end
	end)
	if not ok then
		pcall(function()
			for _, x in ipairs(v) do out[#out + 1] = x end
		end)
	end
	return out
end

-- Converts a map-like native object into a plain table with string keys.
local function map(v)
	local out = {}
	if v == nil then return out end
	pcall(function()
		for k, x in pairs(v) do out[tostring(k)] = x end
	end)
	return out
end

local function num(v)
	if type(v) == "number" then return v end
	return tonumber(tostring(v))
end

local function enumName(enumTable, value)
	if enumTable == nil or value == nil then return value end
	local found
	pcall(function()
		for k, x in pairs(enumTable) do
			if x == value then found = k end
		end
	end)
	return found or num(value) or tostring(value)
end

-- ---------------------------------------------------------------------------

local function collectCargoNames(api, ids)
	local names = {}
	for id in pairs(ids) do
		local name = try("cargoName", function()
			local res = api.res.cargoTypeRep.get(id)
			return res and res.name
		end)
		names[tostring(id)] = name or try("cargoResName", api.res.cargoTypeRep.getName, id) or tostring(id)
	end
	return names
end

local function collectFinance(api, line, now, yearTicks)
	local fin = api.engine.util.finance
	local JE = api.type.JournalEntry
	local maint = JE and JE.Maintenance or {}

	local function balance(from, to, maintType)
		from = math.max(from, 0)
		if to <= from then return 0 end
		if maintType ~= nil then
			return try("calculateBalance", fin.calculateBalance, { line }, from, to, true, maintType)
		end
		return try("calculateBalance", fin.calculateBalance, { line }, from, to, true)
	end

	local function window(from, to)
		local w = {
			net = balance(from, to),
			vehicleRunningCosts = maint.VEHICLE and balance(from, to, maint.VEHICLE) or nil,
			vehicleMaintenance = maint.VEHICLE_MAINTENANCE and balance(from, to, maint.VEHICLE_MAINTENANCE) or nil,
		}
		if w.net then
			-- what is left after removing the (negative) costs is the income
			w.income = w.net - (w.vehicleRunningCosts or 0) - (w.vehicleMaintenance or 0)
		end
		return w
	end

	local monthTicks = yearTicks / 12
	local monthly = {}
	for m = 12, 1, -1 do
		local from = now - m * monthTicks
		if from + monthTicks > 0 then
			local b = balance(from, from + monthTicks)
			monthly[#monthly + 1] = b
		end
	end

	return {
		last12Months = window(now - yearTicks, now),
		previous12Months = window(now - 2 * yearTicks, now - yearTicks),
		monthlyNet = monthly, -- oldest first, last entry = most recent month
	}
end

local function collectVehicle(api, vehicle, now, yearTicks, stateEnum)
	local C = api.type.ComponentType
	local tv = try("getComponent TRANSPORT_VEHICLE", api.engine.getComponent, vehicle, C.TRANSPORT_VEHICLE)
	local v = {
		id = vehicle,
		name = try("vehicleName", api.engine.util.getEntityName, vehicle),
	}
	if tv == nil then return v end

	v.state = enumName(stateEnum, tv.state)
	v.userStopped = tv.userStopped
	v.sellOnArrival = tv.sellOnArrival
	v.noPath = tv.noPath
	v.daysAtTerminal = tv.daysAtTerminal
	v.daysInDepot = tv.daysInDepot
	v.engineStopIndex = tv.stopIndex
	v.autoDeparture = tv.autoDeparture
	v.sectionTimesSec = try("sectionTimes", function() return list(tv.sectionTimes) end)
	v.lastLineStopDeparture = tv.lastLineStopDeparture

	v.capacities = try("capacities", function()
		local caps = {}
		local raw = tv.config and tv.config.capacities
		-- capacities is indexed by cargo type; keep (cargoTypeIndex -> capacity) for non-zero entries
		for i, c in ipairs(list(raw)) do
			if num(c) and num(c) > 0 then caps[tostring(i - 1)] = num(c) end
		end
		return caps
	end)
	-- the engine returns one count per cargo type (1-based, entry i = cargo type i - 1)
	v.loaded = try("getVehicleSimEntitiesCount", function()
		local counts = api.engine.system.simEntityAtVehicleSystem.getVehicleSimEntitiesCount(vehicle)
		if type(counts) == "number" then return counts end
		local total = 0
		for _, c in ipairs(list(counts)) do total = total + (num(c) or 0) end
		return total
	end)
	-- revenue of the whole trip is only credited at final delivery; this is what the vehicle carries until then
	v.pendingIncome = try("unloadPendingIncome", function()
		local p = tv.unloadPendingIncome
		return p and num(p.amount)
	end)
	v.runningCostPerYear = try("getRunningCost", api.engine.util.vehicle.getRunningCost, vehicle)
	v.speed = try("getSpeed", api.engine.util.vehicle.getSpeed, vehicle)

	v.parts = try("parts", function()
		local parts = {}
		local cfg = tv.transportVehicleConfig
		for _, p in ipairs(list(cfg and cfg.vehicles)) do
			local modelId = p.part and p.part.modelId
			parts[#parts + 1] = {
				model = modelId and try("modelName", api.res.modelRep.getName, modelId) or nil,
				ageYears = p.purchaseTime and (now - p.purchaseTime) / yearTicks or nil,
				maintenanceState = p.maintenanceState,
			}
		end
		return parts
	end)
	return v
end

local function pairList(v)
	local r = {}
	for _, p in ipairs(list(v)) do r[#r + 1] = { p[1], p[2] } end
	return r
end

local function collectLineCargo(api, line, cargoId, capacity, used, numStops)
	local sys = api.engine.system
	local entry = { used = used, capacity = capacity }
	local info = try("getLineCargoInfo", sys.transportVehicleSystem.getLineCargoInfo, line, cargoId)
	if info then
		entry.frequency = info.frequency
		entry.numVehicles = info.numVehicles
		entry.totalCapacity = info.totalCapacity
		entry.comfortFactor = info.comfortFactor
		entry.priceFactor = info.priceFactor
		entry.sectionTimesSec = try("sectionTimes", list, info.sectionTimes)
		entry.driveSectionTimesSec = try("driveSectionTimes", list, info.driveSectionTimes)
		-- real times are {time, sampleCount} pairs
		entry.realSectionTimes = try("realSectionTimes", pairList, info.realSectionTimes)
		entry.driveRealSectionTimes = try("driveRealSectionTimes", pairList, info.driveRealSectionTimes)
	end
	entry.waitingPerStop = {}
	for i = 1, numStops do
		entry.waitingPerStop[i] = try("getLineStopSimEntitiesCount",
			sys.simEntityAtTerminalSystem.getLineStopSimEntitiesCount, line, i - 1, cargoId) or 0
	end
	return entry
end

local function collectLine(api, line, now, yearTicks, cargoIdsSeen, stateEnum)
	local C = api.type.ComponentType
	local sys = api.engine.system
	local util = api.engine.util

	local L = {
		id = line,
		name = try("lineName", util.getEntityName, line),
	}

	local comp = try("getComponent LINE", api.engine.getComponent, line, C.LINE)
	local loadModeEnum = try("LoadMode enum", function() return api.type.enum.LineLoadMode end)

	-- stops
	L.stops = {}
	if comp then
		for i, s in ipairs(list(comp.stops)) do
			L.stops[#L.stops + 1] = {
				index = i,
				engineStopIndex = i - 1,
				stationGroup = s.stationGroup,
				stationName = try("stationName", util.getEntityName, s.stationGroup),
				station = s.station,
				terminal = s.terminal,
				alternativeTerminals = try("alternativeTerminals", function() return #list(s.alternativeTerminals) end),
				loadMode = enumName(loadModeEnum, s.loadMode),
				minWaitingTime = s.minWaitingTime,
				maxWaitingTime = s.maxWaitingTime,
				maxAdditionalWaitingTime = s.maxAdditionalWaitingTime,
			}
		end
		L.transportModes = try("transportModes", function()
			local modes = {}
			for k, enabled in pairs(map(comp.vehicleInfo and comp.vehicleInfo.transportModes)) do
				if enabled == true or (num(enabled) or 0) > 0 then modes[#modes + 1] = k end
			end
			table.sort(modes)
			return modes
		end)
	end

	-- headline numbers (same as the game's line window)
	local maxFreq = try("getMaxFrequency", util.line.getMaxFrequency, line)
	L.intervalSec = (maxFreq and maxFreq > 0.0001) and math.floor(1 / maxFreq) or nil
	L.ratePerYear = try("calcLineStationThroughput", util.line.calcLineStationThroughput, line)
	L.transportedPerYear = try("itemsTransported", util.logbook.getLogValuePerYear, line, "itemsTransported")
	L.finance = collectFinance(api, line, now, yearTicks)

	-- per cargo type: capacity use, timings and people/cargo waiting per stop
	L.cargo = {}
	-- the engine returns a 1-based list where entry i is cargo type i - 1 (passengers = type 0 = entry 1)
	local usages = try("getLineCapacityUsages", util.line.getLineCapacityUsages, line, false)
	for index, usage in ipairs(list(usages)) do
		local capacity, used = num(usage and usage.capacity) or 0, num(usage and usage.used) or 0
		if capacity > 0 or used > 0 then
			local cargoId = index - 1
			cargoIdsSeen[cargoId] = true
			L.cargo[tostring(cargoId)] = collectLineCargo(api, line, cargoId, capacity, used, #L.stops)
		end
	end

	-- vehicles
	L.vehicles = {}
	for _, vehicle in ipairs(list(try("getLineVehicles", sys.transportVehicleSystem.getLineVehicles, line))) do
		L.vehicles[#L.vehicles + 1] = collectVehicle(api, vehicle, now, yearTicks, stateEnum)
	end

	-- problems reported by the game itself
	local issueEnum = try("LineIssue enum", function() return api.type.LineIssue and api.type.LineIssue.Type end)
	L.issues = {}
	for _, issue in ipairs(list(try("getLineIssues", util.line.getLineIssues, line, true))) do
		L.issues[#L.issues + 1] = {
			type = enumName(issueEnum, issue.type),
			stopIndex = issue.stopIndex,
			cargoType = issue.cargoType,
		}
	end
	L.stopProblems = {}
	for i, perStop in ipairs(list(try("getDetailedLineProblems", util.line.getDetailedLineProblems, line))) do
		for _, st in ipairs(list(perStop)) do
			local p = {}
			if st.empty then p[#p + 1] = "empty" end
			if st.noPath then p[#p + 1] = "noPath" end
			if st.duplicateStop then p[#p + 1] = "duplicateStop" end
			if st.incompatibleStop then p[#p + 1] = "incompatibleStop" end
			if #p > 0 then L.stopProblems[#L.stopProblems + 1] = { stop = i, problems = p } end
		end
	end

	return L
end

--- Collects the full snapshot.
-- @param api the game api table
-- @return a plain lua table ready for json encoding
function M.collect(api)
	errors = {}
	local util = api.engine.util
	local C = api.type.ComponentType

	local player = try("getPlayer", util.getPlayer)
	local world = try("getWorld", util.getWorld)
	local gt = world and try("getComponent GAME_TIME", api.engine.getComponent, world, C.GAME_TIME)
	local now = gt and gt.gameTime or 0
	local yearTicks = try("getDefaultYearDuration", api.util.getDefaultYearDuration) or (12 * 30 * 2000)

	local snapshot = {
		schemaVersion = M.SCHEMA_VERSION,
		generator = "Line Doctor (olrick_line_doctor_1)",
		gameTime = now,
		yearTicks = yearTicks,
		year = try("getYear", util.getYear),
		date = try("getCalendarDate", function()
			local d = util.getCalendarDate(now)
			return { year = d.year, month = d.month, day = d.day }
		end),
		buildVersion = try("getBuildVersion", function() return getBuildVersion() end),
	}

	snapshot.company = {
		balance = player and try("getPlayersBalance", util.finance.getPlayersBalance, player),
		loan = player and try("account loan", function()
			local acc = api.engine.getComponent(player, C.ACCOUNT)
			return acc and acc.loan
		end),
		earningsThisYear = player and try("calculateEarnings", util.finance.calculateEarnings, player),
	}

	local stateEnum = try("TransportVehicleState enum", function() return api.type.enum.TransportVehicleState end)
	local lineSystem = api.engine.system.lineSystem
	local lines = player and try("getLinesForPlayer", lineSystem.getLinesForPlayer, player)
	if lines == nil then lines = try("getLines", lineSystem.getLines) end

	local cargoIdsSeen = {}
	snapshot.lines = {}
	for _, line in ipairs(list(lines)) do
		snapshot.lines[#snapshot.lines + 1] = collectLine(api, line, now, yearTicks, cargoIdsSeen, stateEnum)
	end
	snapshot.cargoNames = collectCargoNames(api, cargoIdsSeen)
	snapshot.passengerCargoTypeId = try("getPassengerCargoTypeId", api.res.cargoTypeRep.getPassengerCargoTypeId)

	snapshot.network = {
		blockedTrains = try("getBlockedTrains", function()
			return #list(api.engine.system.landVehicleMoveSystem.getBlockedTrains())
		end),
		noPathVehicles = try("getNoPathVehicles", function()
			return #list(api.engine.system.transportVehicleSystem.getNoPathVehicles())
		end),
	}

	snapshot.errors = errors
	errors = nil
	return snapshot
end

return M
