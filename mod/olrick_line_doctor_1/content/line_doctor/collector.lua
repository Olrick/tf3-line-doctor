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
	-- same journal as the "Balance" chart of the game's vehicle window
	v.finance12m = try("vehicle balance", function()
		local fin = api.engine.util.finance
		local maint = api.type.JournalEntry and api.type.JournalEntry.Maintenance or {}
		local from = math.max(now - yearTicks, 0)
		local net = fin.calculateBalance({ vehicle }, from, now, true)
		local running = maint.VEHICLE and fin.calculateBalance({ vehicle }, from, now, true, maint.VEHICLE) or 0
		local upkeep = maint.VEHICLE_MAINTENANCE and fin.calculateBalance({ vehicle }, from, now, true, maint.VEHICLE_MAINTENANCE) or 0
		return { net = net, income = net - running - upkeep, costs = running + upkeep }
	end)
	-- income of the unloading in progress (only set while the vehicle unloads at a terminal);
	-- the api documents it as a "list of pending income": accept a list or a single record
	try("unloadPendingIncome", function()
		local p = tv.unloadPendingIncome
		if p == nil then return end
		local entries = list(p)
		if #entries == 0 then entries = { p } end
		local total, found = 0, false
		for _, e in ipairs(entries) do
			local amount = num(e.amount)
			if amount then
				total = total + amount
				found = true
			end
		end
		if found then
			v.pendingIncome = total
		else
			v.pendingIncomeRaw = tostring(p) -- unknown shape: keep a trace to adapt the collector
		end
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

-- The game's finance table (same data as the vanilla "Finances" window): every journal key with one value
-- per period column, plus totals. Labels are resolved from the JournalEntry enums.
local function collectFinanceTable(api, player, count, interval)
	local fin = api.engine.util.finance
	if fin.computeFinanceTable == nil or api.type.ChartConfig == nil then return nil end
	local JE = api.type.JournalEntry
	local function nameOf(enumTable, value)
		if enumTable == nil then return tostring(value) end
		return enumName(enumTable, value)
	end
	local config = api.type.ChartConfig.new()
	config.count = count or 4
	if interval then config.interval = interval end
	local fd = fin.computeFinanceTable(player, config)
	local out = {
		headers = list(fd.header), total = list(fd.total), balance = list(fd.balance),
		interest = list(fd.interest), loanBorrowing = list(fd.loanBorrowing), loanRepayment = list(fd.loanRepayment),
		entries = {},
	}
	local function addKeyed(source, key, values, carrier)
		local u = fd:unfoldKey(key)
		out.entries[#out.entries + 1] = {
			source = source, carrier = carrier,
			type = nameOf(JE.Type, u[1]), maint = u[2] ~= nil and nameOf(JE.Maintenance, u[2]) or nil,
			construction = u[3] ~= nil and nameOf(JE.Construction, u[3]) or nil,
			values = list(values),
		}
	end
	-- only the carriers the engine reports: asking for a fixed list double counts (TRAM returns ROAD rows)
	local carriers = {}
	fd:foreach_carrier(function(carrier) carriers[#carriers + 1] = carrier end)
	for _, carrier in ipairs(carriers) do
		local carrierName = nameOf(JE.Carrier, carrier)
		fd:foreach_transport(function(key, values) addKeyed("transport", key, values, carrierName) end, carrier)
	end
	fd:foreach_investment(function(key, values) addKeyed("investment", key, values) end)
	fd:foreach_other(function(key, values)
		out.entries[#out.entries + 1] = { source = "other", type = "OTHER", other = nameOf(JE.Other, key), values = list(values) }
	end)
	return out
end

-- Infrastructure upkeep, object by object: every building (station, depot, port, airport, ...) with its
-- maintenance cost and the lines serving it; streets, tracks and anything else summed.
local function kindOf(fileName)
	local f = string.lower(tostring(fileName or ""))
	local function has(x) return f:find(x, 1, true) ~= nil end
	if has("depot") then return "dépôt" end
	if has("maintenance") then return "station de maintenance" end
	if has("airport") or has("airfield") or has("helipad") or has("heliport") then return "aéroport / héliport" end
	if has("harbor") or has("harbour") or has("port") or has("water") then return "port" end
	if has("truck") or has("cargo") then return "gare de marchandises (route)" end
	if has("bus") or has("tram") or has("street") then return "arrêt / gare routière" end
	if has("rail") or has("train") or has("station") then return "gare ferroviaire" end
	if has("warehouse") then return "entrepôt" end
	return "autre bâtiment"
end

local function collectInfrastructure(api, player)
	local C = api.type.ComponentType
	local sys = api.engine.system
	-- The engine refuses to iterate MAINTENANCE_COST entities ("Cannot loop over this component type"),
	-- so buildings are gathered through the accesses it allows.
	local cons, order, via = {}, {}, {}
	local function addCon(con, source)
		if con == nil or con < 0 then return end
		if not cons[con] then
			cons[con] = true
			order[#order + 1] = con
			via[source] = (via[source] or 0) + 1
		end
	end
	pcall(function()
		for _, con in pairs(sys.streetConnectorSystem.getStation2ConstructionMap()) do addCon(con, "stations") end
	end)
	pcall(function()
		sys.vehicleDepotSystem.forEach(function(depot)
			addCon(sys.streetConnectorSystem.getConstructionEntityForDepot(depot), "depots")
		end)
	end)
	-- maintenance stations used by the vehicles
	pcall(function()
		local lines = sys.lineSystem.getLinesForPlayer(player)
		for li = 1, #lines do
			for _, v in ipairs(list(sys.transportVehicleSystem.getLineVehicles(lines[li]))) do
				pcall(function()
					local tv = api.engine.getComponent(v, C.TRANSPORT_VEHICLE)
					if tv and tv.maintenanceStation and tv.maintenanceStation >= 0 then
						addCon(sys.streetConnectorSystem.getConstructionEntityForDepot(tv.maintenanceStation)
							or tv.maintenanceStation, "maintenance")
					end
				end)
			end
		end
	end)

	local out = { buildings = {}, other = { cost = 0, count = 0, samples = {} }, total = 0,
		entities = #order, via = via, costUnknown = 0 }
	for _, e in ipairs(order) do
		pcall(function()
			local con = api.engine.getComponent(e, C.CONSTRUCTION)
			if not con then return end
			local b = { id = e, file = tostring(con.fileName), kind = kindOf(con.fileName),
				name = try("building name", api.engine.util.getEntityName, e), groups = {}, lines = {} }
			local seenGroup, seenLine = {}, {}
			for _, st in ipairs(list(con.stations)) do
				local g = try("station group", sys.stationGroupSystem.getStationGroup, st)
				if g and not seenGroup[g] then
					seenGroup[g] = true
					b.groups[#b.groups + 1] = g
					if #b.groups == 1 then -- the station name the player sees beats the building's technical name
						b.name = try("group name", api.engine.util.getEntityName, g) or b.name
					end
					for _, l in ipairs(list(try("lines of group", sys.lineSystem.getLinesForStationGroup, g))) do
						if not seenLine[l] then
							seenLine[l] = true
							b.lines[#b.lines + 1] = l
						end
					end
				end
			end
			b.depots = #list(con.depots)
			b.pos = try("building position", function()
				local t = con.transf
				return t and { num(t[13]), num(t[14]), num(t[15]) } or nil
			end)
			-- every place the engine may keep the cost: the building, its stations, depots and modules
			local function mcOf(entity)
				local v = 0
				pcall(function()
					local mc = api.engine.getComponent(entity, C.MAINTENANCE_COST)
					if mc then v = num(mc.maintenanceCost) or 0 end
				end)
				return v
			end
			local maint = api.engine.util.maintenance or {}
			local parts = { building = mcOf(e), stations = 0, depots = 0, modules = 0, group = 0, subcon = 0 }
			for _, x in ipairs(list(con.stations)) do parts.stations = parts.stations + mcOf(x) end
			for _, x in ipairs(list(con.depots)) do parts.depots = parts.depots + mcOf(x) end
			for _, x in ipairs(list(con.subconstructions)) do
				parts.modules = parts.modules + mcOf(x)
				if maint.calcMaintenanceForSubconstruction then
					parts.subcon = parts.subcon + (num(try("calcMaintenanceForSubconstruction",
						maint.calcMaintenanceForSubconstruction, x)) or 0)
				end
			end
			for _, g in ipairs(b.groups) do
				if maint.calcMaintenanceForStationGroup then
					parts.group = parts.group + (num(try("calcMaintenanceForStationGroup",
						maint.calcMaintenanceForStationGroup, g)) or 0)
				end
			end
			b.costParts = parts
			b.modules = #list(con.subconstructions)
			-- direct components first; the engine's computations as fallback
			local cost = parts.building + parts.stations + parts.depots + parts.modules
			if cost == 0 then cost = parts.group end
			if cost == 0 then cost = parts.subcon end
			b.cost = cost
			if cost == 0 then out.costUnknown = out.costUnknown + 1 end
			out.total = out.total + cost
			out.buildings[#out.buildings + 1] = b
		end)
	end
	table.sort(out.buildings, function(a, b) return a.cost > b.cost end)
	return out
end

-- ---------------------------------------------------------------------------
-- stocks: warehouses and industries (cargo waiting, flows per year, cargo thrown away)
-- ---------------------------------------------------------------------------

-- Stock list holders are found three ways, the first may be refused by the engine like SIM_CARGO:
-- entities with a STOCK_LIST component, stocks with cargo waiting, stock lists that threw cargo away.
local function collectStocks(api)
	local C = api.type.ComponentType
	local sys, util = api.engine.system, api.engine.util
	local seen, order = {}, {}
	local function add(e)
		e = num(e)
		if e and not seen[e] then seen[e] = true; order[#order + 1] = e end
	end
	pcall(function()
		for _, e in ipairs(list(api.engine.getEntitiesWithComponent(C.STOCK_LIST))) do add(e) end
	end)
	pcall(function()
		for key in pairs(sys.simEntityAtStockSystem.getStock2SimEntityMap()) do add(key[1]) end
	end)
	local thrown = {}
	pcall(function()
		for e, n in pairs(util.stock.getStockListsWithThrownAwayCargo()) do add(e); thrown[num(e)] = num(n) end
	end)

	local storageType = api.type.StockListType and api.type.StockListType.StorageStock or nil
	local out = {}
	for _, e in ipairs(order) do
		pcall(function()
			local sl = api.engine.getComponent(e, C.STOCK_LIST)
			if not sl then return end
			local s = { id = e, name = try("stock name", util.getEntityName, e), stocks = {}, cargo = {},
				thrownAway = thrown[e] }
			local con = api.engine.getComponent(e, C.CONSTRUCTION)
			if not con then
				local ce = try("construction of industry", sys.streetConnectorSystem.getConstructionEntityForIndustry, e)
				if ce and ce >= 0 then con = api.engine.getComponent(ce, C.CONSTRUCTION) end
			end
			if con then
				s.file = tostring(con.fileName)
				s.pos = try("stock position", function()
					local t = con.transf
					return t and { num(t[13]), num(t[14]), num(t[15]) } or nil
				end)
			end
			local cargoTypes = {}
			for i, st in ipairs(list(sl.stocks)) do
				local id = i - 1 -- StockId: index in the list, 0-based like the engine's other ids
				local entry = { stockId = id, type = enumName(api.type.StockListType, st.type), cargoType = num(st.cargoType),
					capacity = num(st.capacity), count = try("getStockCount", sys.simEntityAtStockSystem.getStockCount, e, id) }
				-- api.type.StockListType is nil in game: StorageStock = 2 (Input 0, Output 1), checked 1938 on warehouses
				if (storageType ~= nil and st.type == storageType) or num(st.type) == 2 then s.warehouse = true end
				if entry.cargoType and entry.cargoType >= 0 then cargoTypes[entry.cargoType] = true end
				s.stocks[#s.stocks + 1] = entry
			end
			pcall(function()
				local inOut = util.stock.getInputsOutputsFromRules(e)
				for _, part in ipairs({ inOut[1], inOut[2] }) do
					for _, ct in ipairs(list(part)) do cargoTypes[num(ct)] = true end
				end
			end)
			for ct in pairs(cargoTypes) do
				s.cargo[tostring(ct)] = {
					shipped = try("shipped per year", util.stock.getCargoTypeShippedPerYear, e, ct),
					delivered = try("delivered per year", util.stock.getCargoTypeDeliveredPerYear, e, ct),
					produced = try("produced per year", util.stock.getCargoProducedPerYear, e, ct),
					consumed = try("consumed per year", util.stock.getCargoConsumedPerYear, e, ct),
					maxProduction = try("max production per year", util.stock.getCargoMaxProductionPerYear, e, ct),
				}
			end
			if s.file and s.file:lower():find("warehouse") then s.warehouse = true end
			out[#out + 1] = s
		end)
	end
	return out
end

-- ---------------------------------------------------------------------------
-- health check, shown in the game bar (same thresholds as analyzer/health.py)
-- ---------------------------------------------------------------------------

-- Computed on the last complete months of the finance table, with the transport income projected at the max
-- company rank (TF3 "inflation"): income x multiplier at max rank / current multiplier, costs unchanged.
-- Subsidies are left out (one-off).
M.HEALTH_RATIOS = {
	{ key = "running", name = "Recettes ÷ fonctionnement des véhicules", good = 2.0, bad = 1.7, higher = true, pct = false },
	{ key = "buildings", name = "Entretien des bâtiments ÷ recettes", good = 0.20, bad = 0.28, higher = false, pct = true },
	{ key = "vmaint", name = "Entretien des véhicules ÷ recettes", good = 0.09, bad = 0.11, higher = false, pct = true },
	{ key = "margin", name = "Résultat d'exploitation ÷ recettes", good = 0.10, bad = 0.0, higher = true, pct = true },
}
-- the engine's JournalEntry enums do not iterate in Lua: the finance table holds their numeric values
-- (mapping checked on a real save, see analyzer/analyze.py ENUM_CODES)
local JE_TYPE_CODES = { [4] = "MAINTENANCE", [5] = "INCOME", [7] = "SUBSIDY" }
local JE_MAINT_CODES = { [0] = "VEHICLE", [1] = "INFRASTRUCTURE", [2] = "OTHER", [3] = "VEHICLE_MAINTENANCE" }
local JE_ROAD_OR_TRACK = { [0] = true, [1] = true, STREET = true, ROAD = true, TRACK = true }

local function healthLevel(value, r)
	if value == nil then return "gris" end
	if r.higher then
		return value >= r.good and "vert" or value >= r.bad and "orange" or "rouge"
	end
	return value <= r.good and "vert" or value <= r.bad and "orange" or "rouge"
end

--- Health of the company from a finance table (collectFinanceTable output) and snapshot.company.
-- The last column of the table is the period in progress and is left out.
function M.health(ft, company)
	if ft == nil or ft.entries == nil or ft.headers == nil or #ft.headers == 0 then return nil end
	local last = math.max(#ft.headers - 1, 1)
	local t = { income = 0, running = 0, vmaint = 0, buildings = 0, roads = 0, other = 0 }
	for _, e in ipairs(ft.entries) do
		local sum = 0
		for i = 1, last do sum = sum + (num(e.values and e.values[i]) or 0) end
		local typ = JE_TYPE_CODES[e.type] or e.type
		if typ == "INCOME" then
			t.income = t.income + sum
		elseif typ == "MAINTENANCE" then
			local maint = JE_MAINT_CODES[e.maint] or e.maint
			if maint == "VEHICLE" then
				t.running = t.running + sum
			elseif maint == "VEHICLE_MAINTENANCE" then
				t.vmaint = t.vmaint + sum
			elseif maint == "INFRASTRUCTURE" then
				local k = JE_ROAD_OR_TRACK[e.construction] and "roads" or "buildings"
				t[k] = t[k] + sum
			else
				t.other = t.other + sum
			end
		end
	end
	company = company or {}
	local current = num(company.priceMultiplier) or 1
	local atMax = num(company.priceMultiplierAtMaxRank) or current
	local scale = (current > 0) and (atMax / current) or 1
	local income = t.income * scale
	local values = {}
	if income > 0 then
		values.running = (t.running < 0) and (income / -t.running) or nil
		values.buildings = -t.buildings / income
		values.vmaint = -t.vmaint / income
		values.margin = (income + t.running + t.vmaint + t.buildings + t.roads + t.other) / income
	end
	local out = {
		periods = last, firstPeriod = ft.headers[1], lastPeriod = ft.headers[last],
		rank = company.rank, maxRank = company.maxRank or 15,
		multiplier = current, multiplierAtMaxRank = atMax, scale = scale,
		incomeNow = t.income, incomeAtMaxRank = income, ratios = {}, worst = "vert",
	}
	local order = { vert = 1, gris = 2, orange = 3, rouge = 4 }
	for _, r in ipairs(M.HEALTH_RATIOS) do
		local v = values[r.key]
		local lvl = healthLevel(v, r)
		out.ratios[#out.ratios + 1] = { key = r.key, name = r.name, value = v, level = lvl, good = r.good, bad = r.bad,
			higher = r.higher, pct = r.pct }
		if order[lvl] > order[out.worst] then out.worst = lvl end
	end
	return out
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
	-- company rank and TF3 "inflation": ticket prices are multiplied by 1 - (rank - 1) x (1 - floor) / 14,
	-- floor set by the game option (None 1, Low 0.9, Normal 0.75, High 0.6, Very high 0.5)
	pcall(function()
		local e = api.engine.system.gameScriptSystem.getEntityForGameScript("::game_mechanics/company/company_progression.gs")
		local gs = api.engine.getComponent(e, C.GAME_SCRIPT)
		local data = gs.state.companyState[player]
		snapshot.company.rank = data.level
		snapshot.company.potentialRank = data.potentialLevel
		snapshot.company.experience = data.experience
		snapshot.company.priceMultiplier = num(data.ticketPriceMultiplier) or 1.0
	end)
	pcall(function()
		local option = api.engine.config.getModParams()[""]["advancedOptions.inflationFactor"]
		snapshot.company.inflationOption = option
		snapshot.company.priceMultiplierAtMaxRank = ({ 1, 0.9, 0.75, 0.6, 0.5 })[option]
	end)
	snapshot.company.maxRank = 15

	snapshot.financeTable = player and try("computeFinanceTable", collectFinanceTable, api, player)
	-- the whole company history, one column per simulated year (the engine keeps its journal since the start)
	snapshot.financeHistory = player and try("finance history", collectFinanceTable, api, player, 150, yearTicks)
	-- the last 12 months, one column per month (+ the month in progress), for the in-game health check
	local monthTicks = try("getDefaultMonthDuration", api.util.getDefaultMonthDuration) or (yearTicks / 12)
	snapshot.financeLast12Months = player and try("finance last 12 months", collectFinanceTable, api, player, 13, monthTicks)
	snapshot.health = try("health", M.health, snapshot.financeLast12Months, snapshot.company)
	snapshot.infrastructure = player and try("infrastructure", collectInfrastructure, api, player)
	snapshot.stocks = try("stocks", collectStocks, api)

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
