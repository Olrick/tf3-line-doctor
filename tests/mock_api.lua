-- Mocked subset of the TF3 api used by the Line Doctor collector.
-- Two lines:
--   100 "Bus 1"   : 4 buses, little traffic, loses money       -> OVERCAPACITY expected
--   200 "Coal A"  : 2 trucks, crowded stop, profitable          -> UNDERCAPACITY expected
-- One api call (getBlockedTrains) throws, to check error capture.

local YEAR = 1461000 -- value observed in game
local NUM_CARGO_TYPES = 37
local NOW = 5 * YEAR

local C = { LINE = 1, GAME_TIME = 2, TRANSPORT_VEHICLE = 3, ACCOUNT = 4 }
local MAINT = { VEHICLE = 10, VEHICLE_MAINTENANCE = 11 }
local STATE = { IN_DEPOT = 0, EN_ROUTE = 1, AT_TERMINAL = 2, GOING_TO_DEPOT = 3 }

local lines = {
	[100] = {
		name = "Bus 1",
		stops = { { stationGroup = 1001, name = "Gare" }, { stationGroup = 1002, name = "Mairie" } },
		vehicles = { 11, 12, 13, 14 },
		capacity = { [0] = 160 }, used = { [0] = 20 },
		income = 30000, running = -50000, maint = -10000,
		rate = 20000, transported = 3000, waiting = { 2, 1 },
		real = { { 120, 5 }, { 130, 5 } }, driveReal = { { 100, 5 }, { 105, 5 } }, drive = { 95, 100 },
	},
	[200] = {
		name = "Coal A",
		stops = { { stationGroup = 2001, name = "Mine" }, { stationGroup = 2002, name = "Steel" } },
		vehicles = { 21, 22 },
		capacity = { [5] = 40 }, used = { [5] = 38 },
		income = 200000, running = -40000, maint = -5000,
		rate = 10000, transported = 9000, waiting = { 150, 0 },
		real = { { 300, 3 }, { 280, 3 } }, driveReal = { { 250, 3 }, { 240, 3 } }, drive = { 240, 235 },
	},
}

local vehicles = {
	[11] = { line = 100, cap = { 40 }, loaded = 5, age = 30 }, [12] = { line = 100, cap = { 40 }, loaded = 5, age = 30 },
	[13] = { line = 100, cap = { 40 }, loaded = 5, age = 30 }, [14] = { line = 100, cap = { 40 }, loaded = 5, age = 30, stopped = true },
	[21] = { line = 200, cap = { 0, 0, 0, 0, 0, 20 }, loaded = 19, age = 2, pending = 1234 },
	[22] = { line = 200, cap = { 0, 0, 0, 0, 0, 20 }, loaded = 19, age = 2 },
}

local names = { [1001] = "Gare", [1002] = "Mairie", [2001] = "Mine", [2002] = "Steel" }
for id, l in pairs(lines) do names[id] = l.name end
for id in pairs(vehicles) do names[id] = "Vehicle " .. id end

local function component(e, t)
	if t == C.GAME_TIME and e == 1 then return { gameTime = NOW } end
	if t == C.ACCOUNT and e == 2 then return { balance = 1500000, loan = 500000 } end
	if t == C.LINE and lines[e] then
		local stops = {}
		for _, s in ipairs(lines[e].stops) do
			stops[#stops + 1] = { stationGroup = s.stationGroup, station = 0, terminal = 0, alternativeTerminals = {},
				loadMode = 0, minWaitingTime = 0, maxWaitingTime = 180, maxAdditionalWaitingTime = 0 }
		end
		return { stops = stops, vehicleInfo = { transportModes = { BUS = true } } }
	end
	if t == C.TRANSPORT_VEHICLE and vehicles[e] then
		local v = vehicles[e]
		return {
			state = STATE.EN_ROUTE, userStopped = v.stopped or false, sellOnArrival = false, noPath = false,
			daysAtTerminal = 0, daysInDepot = 0, stopIndex = 0, autoDeparture = true,
			sectionTimes = { 100, 100 }, lastLineStopDeparture = NOW - 1000,
			config = { capacities = v.cap },
			unloadPendingIncome = { { amount = v.pending or 0, lineEntity = v.line } }, -- a list, like the engine
			transportVehicleConfig = { vehicles = { { part = { modelId = 7 }, purchaseTime = NOW - v.age * YEAR, maintenanceState = 0.9 } } },
		}
	end
	return nil
end

-- accounts exist for lines and for vehicles (a vehicle gets an equal share of its line)
local function lineOf(ents)
	local e = ents[1]
	if lines[e] then return lines[e] end
	local l = lines[vehicles[e].line]
	local n = #l.vehicles
	return { income = l.income / n, running = l.running / n, maint = l.maint / n }
end

return {
	type = {
		ComponentType = C,
		JournalEntry = { Maintenance = MAINT },
		enum = { TransportVehicleState = STATE },
	},
	util = {
		getDefaultYearDuration = function() return YEAR end,
		getDefaultMonthDuration = function() return YEAR / 12 end,
	},
	res = {
		cargoTypeRep = {
			get = function(id)
				if id < 0 or id >= NUM_CARGO_TYPES then error("Index " .. id .. " out of bounds") end
				return { name = (id == 0) and "Passengers" or "Coal" }
			end,
			getName = function(id) return "cargo_" .. id end,
			getPassengerCargoTypeId = function() return 0 end,
		},
		modelRep = { getName = function(id) return "model_" .. id .. ".mdl" end },
	},
	engine = {
		getComponent = component,
		util = {
			getPlayer = function() return 2 end,
			getWorld = function() return 1 end,
			getYear = function() return 1905 end,
			getCalendarDate = function() return { year = 1905, month = 3, day = 14 } end,
			getEntityName = function(e) return names[e] or ("#" .. e) end,
			finance = {
				calculateBalance = function(ents, from, to, _maintOnly, maintType)
					local l = lineOf(ents)
					local share = (to - from) / YEAR
					if from < NOW - YEAR then share = share * 0.8 end -- previous year was a bit worse
					if maintType == MAINT.VEHICLE then return math.floor(l.running * share) end
					if maintType == MAINT.VEHICLE_MAINTENANCE then return math.floor(l.maint * share) end
					return math.floor((l.income + l.running + l.maint) * share)
				end,
				getPlayersBalance = function() return 1500000 end,
				calculateEarnings = function() return 42000 end,
			},
			line = {
				getMaxFrequency = function(line) return line == 100 and 1 / 900 or 1 / 240 end,
				calcLineStationThroughput = function(line) return lines[line].rate end,
				-- like the engine: 1-based list over ALL cargo types, entry i = cargo type i - 1
				getLineCapacityUsages = function(line)
					local l, out = lines[line], {}
					for i = 1, NUM_CARGO_TYPES do
						out[i] = { used = l.used[i - 1] or 0, capacity = l.capacity[i - 1] or 0 }
					end
					return out
				end,
				getLineIssues = function() return {} end,
				getDetailedLineProblems = function() return { {}, {} } end,
			},
			logbook = {
				getLogValuePerYear = function(line, name)
					assert(name == "itemsTransported")
					return lines[line].transported
				end,
			},
			vehicle = {
				getRunningCost = function() return 12000 end,
				getSpeed = function() return 15 end,
			},
		},
		system = {
			lineSystem = { getLinesForPlayer = function() return { 100, 200 } end },
			transportVehicleSystem = {
				getLineVehicles = function(line) return lines[line].vehicles end,
				getLineCargoInfo = function(line)
					local l = lines[line]
					return { frequency = 0.01, numVehicles = #l.vehicles, totalCapacity = 160, comfortFactor = 1, priceFactor = 1,
						sectionTimes = { 100, 100 }, driveSectionTimes = l.drive, realSectionTimes = l.real,
						driveRealSectionTimes = l.driveReal }
				end,
				getNoPathVehicles = function() return {} end,
			},
			simEntityAtTerminalSystem = {
				getLineStopSimEntitiesCount = function(line, stopIndex) return lines[line].waiting[stopIndex + 1] end,
			},
			simEntityAtVehicleSystem = {
				-- like the engine: one count per cargo type
				getVehicleSimEntitiesCount = function(v)
					local out = {}
					for i = 1, NUM_CARGO_TYPES do out[i] = 0 end
					out[1] = vehicles[v].loaded
					return out
				end,
			},
			landVehicleMoveSystem = {
				getBlockedTrains = function() error("not available in mock") end,
			},
		},
	},
}
