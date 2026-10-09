-- Mocked subset of the TF3 api used by the Line Doctor collector.
-- Two lines:
--   100 "Bus 1"   : 4 buses, little traffic, loses money       -> OVERCAPACITY expected
--   200 "Coal A"  : 2 trucks, crowded stop, profitable          -> UNDERCAPACITY expected
-- One api call (getBlockedTrains) throws, to check error capture.

local YEAR = 1461000 -- value observed in game
local NUM_CARGO_TYPES = 37
local NOW = 5 * YEAR

local C = { LINE = 1, GAME_TIME = 2, TRANSPORT_VEHICLE = 3, ACCOUNT = 4, SIM_CARGO = 5, SIM_ENTITY_AT_VEHICLE = 6, CONSTRUCTION = 7, SIM_ENTITY_AT_TERMINAL = 8, MAINTENANCE_COST = 9, BASE_EDGE = 10, BASE_EDGE_STREET = 11, GAME_SCRIPT = 12, STOCK_LIST = 13 }

-- a coal unit delivered by line 200 after a first leg on line 100 (sim 9002)
local simCargo = {
	-- unloaded by line 100 at line 200's first stop, waiting there; customer 88 has no construction
	[9003] = {
		cargoType = 5, startTime = NOW - 30000, pickupTime = NOW - 20000, deliveryExtensionDuration = 0,
		sourceEntity = 501, targetOrPickupEntity = 88,
		pickupPoints = { { line = 100, vehicle = 12, carrier = 0, position = { x = 0, y = 0, z = 0 } } },
	},
	[9002] = {
		cargoType = 5, startTime = NOW - 50000, pickupTime = NOW - 40000, deliveryExtensionDuration = 0,
		sourceEntity = 501, targetOrPickupEntity = 77,
		pickupPoints = {
			{ line = 100, vehicle = 11, carrier = 0, position = { x = 0, y = 0, z = 0 } },
			{ line = 200, vehicle = 22, carrier = 0, position = { x = 300, y = 400, z = 0 } },
		},
	},
}
local MAINT = { VEHICLE = 10, VEHICLE_MAINTENANCE = 11, INFRASTRUCTURE = 12 }
local JE_TYPE = { INCOME = 1, MAINTENANCE = 2, CONSTRUCTION = 3 }
local JE_CONSTRUCTION = { TRACK = 20 }
local JE_CARRIER = { ROAD = 30 }

-- finance table: 2 periods; road income, road vehicle running costs, road infrastructure upkeep, track building
local financeKeys = {
	[1] = { JE_TYPE.INCOME, nil, nil }, [2] = { JE_TYPE.MAINTENANCE, MAINT.VEHICLE, nil },
	[3] = { JE_TYPE.MAINTENANCE, MAINT.INFRASTRUCTURE, nil }, [4] = { JE_TYPE.CONSTRUCTION, nil, JE_CONSTRUCTION.TRACK },
}
local financeData = {
	header = { "1981", "1982" }, total = { -5000, -8000 }, balance = { 100000, 92000 },
	interest = { 0, 0 }, loanBorrowing = { 0, 0 }, loanRepayment = { 0, 0 },
	unfoldKey = function(_, key) return financeKeys[key] end,
	foreach_carrier = function(_, fn) fn(JE_CARRIER.ROAD) end,
	foreach_transport = function(_, fn, carrier)
		if carrier == JE_CARRIER.ROAD then
			fn(1, { 40000, 42000 }); fn(2, { -20000, -21000 }); fn(3, { -25000, -27000 })
		end
	end,
	foreach_investment = function(_, fn) fn(4, { 0, -2000 }) end,
	foreach_other = function(_, _fn) end,
}
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
	[21] = { line = 200, cap = { 0, 0, 0, 0, 0, 20 }, loaded = 19, age = 2, pending = 1234, state = 2, stop = 0 },
	[22] = { line = 200, cap = { 0, 0, 0, 0, 0, 20 }, loaded = 19, age = 2 },
}

local names = { [1001] = "Gare", [1002] = "Mairie", [2001] = "Mine", [2002] = "Steel" }
for id, l in pairs(lines) do names[id] = l.name end
for id in pairs(vehicles) do names[id] = "Vehicle " .. id end

-- destination of sim 9002 (a construction): translation at transf[13..15]
local constructions = {
	[77] = { transf = { 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 1500, 2000, 10, 1 } },
	-- a bus station used by line 100 and an unused truck station
	[601] = { fileName = "station/street/bus_station.con", stations = { 701 }, depots = {},
		transf = { 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 100, 100, 10, 1 } },
	[602] = { fileName = "station/street/truck_station.con", stations = { 702 }, depots = {},
		transf = { 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 130, 140, 2, 1 } },
}
local maintenance = { [601] = 30000, [602] = 50000, [801] = 2000, [802] = 3000 } -- 801 street, 802 track
local stationGroupOf = { [701] = 1001, [702] = 2999 }

local function component(e, t)
	if t == C.CONSTRUCTION then return constructions[e] end
	if t == C.MAINTENANCE_COST and maintenance[e] then return { maintenanceCost = maintenance[e] } end
	if t == C.BASE_EDGE and (e == 801 or e == 802) then return {} end
	if t == C.BASE_EDGE_STREET and e == 801 then return {} end
	if t == C.SIM_CARGO then return simCargo[e] end
	if t == C.SIM_ENTITY_AT_VEHICLE and e == 9002 then return { line = 200, vehicle = 22 } end
	if t == C.SIM_ENTITY_AT_TERMINAL and e == 9003 then return { line = 200, lineStop0 = 0, lineStop1 = 1 } end
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
			state = v.state or STATE.EN_ROUTE, userStopped = v.stopped or false, sellOnArrival = false, noPath = false,
			daysAtTerminal = 0, daysInDepot = 0, stopIndex = v.stop or 0, autoDeparture = true,
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
		StockListType = { InputStock = 0, OutputStock = 1, StorageStock = 2 },
		JournalEntry = { Maintenance = MAINT, Type = JE_TYPE, Construction = JE_CONSTRUCTION, Carrier = JE_CARRIER, Other = {} },
		ChartConfig = { new = function() return {} end },
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
		getComponent = function(e, t)
			if t == C.GAME_SCRIPT and e == 950 then
				return { state = { companyState = { [2] = { level = 12, potentialLevel = 12, experience = 123456,
					ticketPriceMultiplier = 0.8036 } } } }
			end
			-- warehouse 801: 150 bricks (cargo 33) stored, cargo thrown away
			if e == 801 and t == C.STOCK_LIST then
				return { stocks = { { type = 2, cargoType = 33, capacity = 200 }, { type = 2, cargoType = -1, capacity = 200 } } }
			end
			if e == 801 and t == C.CONSTRUCTION then
				return { fileName = "warehouse/warehouse_small.con", transf = { 1,0,0,0, 0,1,0,0, 0,0,1,0, 1300,3100,5,1 } }
			end
			return component(e, t)
		end,
		config = { getModParams = function() return { [""] = { ["advancedOptions.inflationFactor"] = 3 } } end },
		getEntitiesWithComponent = function(t)
			-- like the engine: sim entities cannot be iterated this way
			if t == C.SIM_CARGO then error("Cannot loop over this component type") end
			if t == C.MAINTENANCE_COST then error("Cannot loop over this component type") end
			if t == C.STOCK_LIST then error("Cannot loop over this component type") end -- assumed: found via the systems
			return {}
		end,
		entityExists = function() return true end,
		util = {
			getPlayer = function() return 2 end,
			getWorld = function() return 1 end,
			getYear = function() return 1905 end,
			getCalendarDate = function() return { year = 1905, month = 3, day = 14 } end,
			getEntityName = function(e) return names[e] or ("#" .. e) end,
			stock = {
				getStockListsWithThrownAwayCargo = function() return { [801] = 12 } end,
				getInputsOutputsFromRules = function() return { {}, {} } end,
				getCargoTypeShippedPerYear = function(e, ct) return ct == 33 and 90 or 0 end,
				getCargoTypeDeliveredPerYear = function(e, ct) return ct == 33 and 160 or 0 end,
				getCargoProducedPerYear = function() return 0 end,
				getCargoConsumedPerYear = function() return 0 end,
				getCargoMaxProductionPerYear = function() return 0 end,
			},
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
				computeFinanceTable = function() return financeData end,
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
				getPosition = function(v)
					if v == 21 then return { x = 300, y = 400, z = 0 } end -- standing at line 200's first stop
					return { x = 900, y = 1200, z = 10 }
				end,
			},
		},
		system = {
			vehicleDepotSystem = { forEach = function(fn) end },
			gameScriptSystem = { getEntityForGameScript = function() return 950 end },
			townBuildingSystem = {
				getTown2BuildingMap = function() return { [300] = { 3001, 3002 } } end,
				getTown2personCapacitiesMap = function() return { [300] = { 1200, 300, 150 } } end,
				getCargoSupplyAndLimit = function(town) return { [5] = { 12, 40, 0 } } end,
			},
			simEntityAtStockSystem = {
				getStock2SimEntityMap = function() return { [{ 801, 0 }] = { 7001, 7002 } } end,
				getStockCount = function(e, id) return (e == 801 and id == 0) and 150 or 0 end,
			},
			streetConnectorSystem = {
				getStation2ConstructionMap = function() return { [701] = 601, [702] = 602 } end,
				getConstructionEntityForDepot = function() return -1 end,
				getConstructionEntityForIndustry = function() return -1 end,
				getConstructionEntityForTownBuilding = function() return -1 end,
				getConstructionEntityForSubconstruction = function() return -1 end,
				getConstructionEntityForStation = function() return -1 end,
			},
			lineSystem = {
				getLinesForPlayer = function() return { 100, 200 } end,
				getLinesForStationGroup = function(g) if g == 1001 then return { 100 } end return {} end,
			},
			stationGroupSystem = { getStationGroup = function(st) return stationGroupOf[st] end },
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
				getLineStopSimEntities = function(line, stopIndex, cargoType)
					if line == 200 and stopIndex == 0 and cargoType == 5 then return { 9002, 9003 } end -- 9002: duplicate on purpose
					return {}
				end,
				getLineStopSimEntitiesCount = function(line, stopIndex) return lines[line].waiting[stopIndex + 1] end,
			},
			simEntityAtVehicleSystem = {
				-- vehicle -> cargo type -> sims on board (sim 9002 is in vehicle 22, coal = 5; a passenger in 11)
				getVehicle2Cargo2SimEntitesMap = function() return { [22] = { [5] = { 9002 } }, [11] = { [0] = { 7001 } } } end,
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
