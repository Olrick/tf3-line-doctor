-- Ticket price probe: observes the engine's price events to learn when and how much each line earns.
--
-- Purely observational: handlers never return a value, so ticket prices are never modified.
-- Everything is kept in the game-script state (several Lua states run the script, file-level variables
-- are not shared), bounded so the save game does not grow:
--   s.probe = {
--     from = <game time the period started>,
--     lines = { [lineId] = { events, final, transfer, base, dist, passenger } },
--     samples = { ... up to MAX_SAMPLES ticket events with context },
--     arrivals = { cargo = n, person = n, sampled = { ... } },
--     watch = { [simEntity] = <game time of its first ticket event> }  (only sampled entities)
--   }
-- The game api is passed in, so the module can be unit-tested with a mocked api.

local M = {}

M.MAX_SAMPLES = 40
M.MAX_WATCH = 200
M.MAX_ARRIVAL_SAMPLES = 40

local function num(v)
	if type(v) == "number" then return v end
	return tonumber(tostring(v))
end

local function probeState(s, now)
	if s.probe == nil then
		s.probe = { from = now, lines = {}, samples = {}, arrivals = { cargo = 0, person = 0, sampled = {} }, watch = {} }
	end
	return s.probe
end

local function context(api, sim)
	local C = api.type.ComponentType
	local ctx = {}
	pcall(function() ctx.exists = api.engine.entityExists(sim) end)
	pcall(function() ctx.atVehicle = api.engine.getComponent(sim, C.SIM_ENTITY_AT_VEHICLE) ~= nil end)
	pcall(function() ctx.atTerminal = api.engine.getComponent(sim, C.SIM_ENTITY_AT_TERMINAL) ~= nil end)
	pcall(function()
		local cargo = api.engine.getComponent(sim, C.SIM_CARGO)
		if cargo then
			local n = 0
			pcall(function() n = #cargo.pickupPoints end)
			ctx.pickupPoints = n
			ctx.cargoType = cargo.cargoType
		end
	end)
	return ctx
end

--- Handles an OnCalcTicketPrice event (cargo: TransportVehicleSystem, passengers: SimEntityAtVehicleSystem).
function M.onTicketPrice(api, s, id, params, now)
	local p = probeState(s, now)
	local passenger = id == "SimEntityAtVehicleSystem"
	local n = 0
	pcall(function() n = #params end)
	for i = 1, n do
		local e = params[i]
		local line = e.lineEntity
		local key = tostring(line)
		local L = p.lines[key]
		if L == nil then
			L = { events = 0, final = 0, transfer = 0, base = 0, dist = 0, passenger = 0 }
			p.lines[key] = L
		end
		local stock = num(e.stockListEntity)
		L.events = L.events + 1
		L.base = L.base + (num(e.basePrice) or 0)
		L.dist = L.dist + (num(e.distance) or 0)
		if passenger then
			L.passenger = L.passenger + 1
		elseif stock and stock >= 0 then
			L.final = L.final + 1
		else
			L.transfer = L.transfer + 1
		end

		if #p.samples < M.MAX_SAMPLES then
			local sample = {
				t = now, source = id, line = line, vehicle = e.vehicleEntity, sim = e.simEntity,
				stock = stock, basePrice = num(e.basePrice), distance = num(e.distance),
				context = context(api, e.simEntity),
			}
			p.samples[#p.samples + 1] = sample
			local count = 0
			for _ in pairs(p.watch) do count = count + 1 end
			if count < M.MAX_WATCH and e.simEntity ~= nil and p.watch[tostring(e.simEntity)] == nil then
				p.watch[tostring(e.simEntity)] = now
			end
		end
	end
end

--- Handles OnToArriveAtDestination (SimCargoSystem: cargo, SimPersonSystem: persons).
function M.onArrive(api, s, id, param, now)
	local p = probeState(s, now)
	local entities = {}
	pcall(function() entities = param.entities or {} end)
	local isCargo = id == "SimCargoSystem"
	local n = 0
	pcall(function() n = #entities end)
	if isCargo then p.arrivals.cargo = p.arrivals.cargo + n else p.arrivals.person = p.arrivals.person + n end
	for i = 1, n do
		local item = entities[i]
		local sim = item
		if type(item) == "table" or type(item) == "userdata" then pcall(function() sim = item[1] end) end
		local key = tostring(sim)
		local ticketTime = p.watch[key]
		if ticketTime ~= nil then
			p.watch[key] = nil
			if #p.arrivals.sampled < M.MAX_ARRIVAL_SAMPLES then
				p.arrivals.sampled[#p.arrivals.sampled + 1] = {
					sim = sim, source = id, ticketTime = ticketTime, arrivalTime = now, delay = now - ticketTime,
				}
			end
		end
	end
end

--- Returns the probe data of the current period, with the journal income of each line over the same
--- period, and starts a new period.
function M.takeSummary(api, s, now)
	local p = s.probe
	if p == nil then return nil end
	local fin = api.engine.util.finance
	local maint = api.type.JournalEntry and api.type.JournalEntry.Maintenance or {}
	local lines = {}
	for key, L in pairs(p.lines) do
		local line = tonumber(key)
		local entry = {
			events = L.events, final = L.final, transfer = L.transfer, passenger = L.passenger,
			basePriceSum = L.base, distanceSum = L.dist,
		}
		pcall(function() entry.name = api.engine.util.getEntityName(line) end)
		pcall(function()
			local net = fin.calculateBalance({ line }, p.from, now, true)
			local running = maint.VEHICLE and fin.calculateBalance({ line }, p.from, now, true, maint.VEHICLE) or 0
			local upkeep = maint.VEHICLE_MAINTENANCE
				and fin.calculateBalance({ line }, p.from, now, true, maint.VEHICLE_MAINTENANCE) or 0
			entry.journalIncome = net - running - upkeep
		end)
		lines[key] = entry
	end
	local pendingWatch = 0
	for _ in pairs(p.watch) do pendingWatch = pendingWatch + 1 end
	local summary = {
		from = p.from, to = now, lines = lines, samples = p.samples, arrivals = p.arrivals,
		watchedWithoutArrival = pendingWatch,
	}
	-- keep watching sampled entities across periods, reset the rest
	s.probe = { from = now, lines = {}, samples = {}, arrivals = { cargo = 0, person = 0, sampled = {} }, watch = p.watch }
	return summary
end

return M
