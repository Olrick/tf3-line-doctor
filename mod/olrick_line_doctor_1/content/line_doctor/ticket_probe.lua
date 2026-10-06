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
local summary_ratio

M.MAX_SAMPLES = 40
M.MAX_WATCH = 200
M.MAX_ARRIVAL_SAMPLES = 40
M.MAX_CHAIN_SAMPLES = 40   -- delivered cargo that used several lines (how the price is shared)
M.MAX_SINGLE_SAMPLES = 20  -- delivered cargo that used one line (how the price is computed)

local function num(v)
	if type(v) == "number" then return v end
	return tonumber(tostring(v))
end

local function newPeriod(now, watch)
	return { from = now, lines = {}, samples = {}, chains = {}, singles = {},
		arrivals = { cargo = 0, person = 0, sampled = {} }, watch = watch or {} }
end

local function probeState(s, now)
	if s.probe == nil then s.probe = newPeriod(now) end
	s.probe.chains = s.probe.chains or {}
	s.probe.singles = s.probe.singles or {}
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

local function vec(v)
	if v == nil then return nil end
	local out
	pcall(function() out = { num(v.x), num(v.y), num(v.z) } end)
	if out == nil or out[1] == nil then pcall(function() out = { num(v[1]), num(v[2]), num(v[3]) } end) end
	return out
end

-- Everything needed to rebuild the price of a delivered cargo item offline.
local function cargoDetail(api, sim, basePrice, line, now)
	local C = api.type.ComponentType
	local d = { sim = sim, basePrice = basePrice, line = line, t = now }
	pcall(function()
		local cargo = api.engine.getComponent(sim, C.SIM_CARGO)
		d.cargoType = cargo.cargoType
		d.startTime = cargo.startTime
		d.pickupTime = cargo.pickupTime
		d.deliveryExtension = cargo.deliveryExtensionDuration
		d.source = cargo.sourceEntity
		d.target = cargo.targetOrPickupEntity
		d.segments = {}
		for i = 1, #cargo.pickupPoints do
			local pp = cargo.pickupPoints[i]
			d.segments[#d.segments + 1] = { line = pp.line, vehicle = pp.vehicle, carrier = pp.carrier, pos = vec(pp.position) }
		end
	end)
	pcall(function()
		local atVehicle = api.engine.getComponent(sim, C.SIM_ENTITY_AT_VEHICLE)
		d.vehicle = atVehicle.vehicle
		d.unloadPos = vec(api.engine.util.vehicle.getPosition(atVehicle.vehicle))
	end)
	return d
end

--- Handles an OnCalcTicketPrice event (cargo: TransportVehicleSystem, passengers: SimEntityAtVehicleSystem).
-- @param onDelivered optional function(detail) called for every delivered cargo item
function M.onTicketPrice(api, s, id, params, now, onDelivered)
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

		local d
		if not passenger and stock and stock >= 0 and onDelivered then
			d = cargoDetail(api, e.simEntity, num(e.basePrice), line, now)
			pcall(onDelivered, d)
		end
		if not passenger and stock and stock >= 0
				and (#p.chains < M.MAX_CHAIN_SAMPLES or #p.singles < M.MAX_SINGLE_SAMPLES) then
			d = d or cargoDetail(api, e.simEntity, num(e.basePrice), line, now)
			local nseg = d.segments and #d.segments or 0
			if nseg >= 2 and #p.chains < M.MAX_CHAIN_SAMPLES then
				p.chains[#p.chains + 1] = d
			elseif nseg == 1 and #p.singles < M.MAX_SINGLE_SAMPLES then
				p.singles[#p.singles + 1] = d
			end
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
	-- passenger-only lines are paid right away: their booked/price ratio is the game's multiplier
	local pj, pb = 0, 0
	for _, e in pairs(lines) do
		if e.passenger > 0 and e.final == 0 and e.journalIncome then
			pj, pb = pj + e.journalIncome, pb + e.basePriceSum
		end
	end
	if pb > 0 then summary_ratio = { journal = pj, price = pb } end
	local pendingWatch = 0
	for _ in pairs(p.watch) do pendingWatch = pendingWatch + 1 end
	local summary = {
		from = p.from, to = now, lines = lines, samples = p.samples, arrivals = p.arrivals,
		watchedWithoutArrival = pendingWatch, chains = p.chains or {}, singles = p.singles or {},
		passengerRatio = summary_ratio,
	}
	summary_ratio = nil
	-- keep watching sampled entities across periods, reset the rest
	s.probe = newPeriod(now, p.watch)
	return summary
end

return M
