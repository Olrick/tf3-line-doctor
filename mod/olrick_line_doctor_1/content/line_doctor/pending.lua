-- Pending (not yet booked) income per line.
--
-- How TF3 pays cargo (measured in game with ticket_probe.lua, 2026-10-06):
--   * the price is computed once, on delivery to the final customer, and booked then;
--   * price = factor(cargo type) x straight distance (first pickup -> delivery, +8x climb), and the booked
--     income is ~0.804 x that price (ratio measured on the save, kept up to date here);
--   * every line of the chain gets a share proportional to the straight length of its own segment.
-- Passengers are paid at the end of each ride: they never have pending income.
--
-- For every cargo item still in the network this module estimates the final price and gives each line
-- the share of the segments it has already completed ("done") or is driving right now ("inProgress").
-- Calibration (factor per cargo type, booked/price ratio) is learned from real deliveries and stored in
-- the game-script state: s.calib = { types = { [cargoType] = { f, n } }, k = { v, n } }.

local M = {}

M.DEFAULT_FACTOR = 3.9  -- price per metre when a cargo type was never delivered yet (measured 3.3-5.0)
M.DEFAULT_K = 0.804     -- booked income / computed price (measured)
M.EMA = 0.1             -- weight of a new measurement

local function num(v)
	if type(v) == "number" then return v end
	return tonumber(tostring(v))
end

local function vec(v)
	if v == nil then return nil end
	local out
	pcall(function() out = { num(v.x), num(v.y), num(v.z) } end)
	if out == nil or out[1] == nil then pcall(function() out = { num(v[1]), num(v[2]), num(v[3]) } end) end
	if out == nil or out[1] == nil then return nil end
	return out
end

--- Distance used by the game's income script: straight line + 8 x climb.
function M.dist(a, b)
	local dx, dy, dz = b[1] - a[1], b[2] - a[2], (b[3] or 0) - (a[3] or 0)
	return math.sqrt(dx * dx + dy * dy + dz * dz) + 8 * math.max(dz, 0)
end

local function transfPos(api, construction)
	local c = api.engine.getComponent(construction, api.type.ComponentType.CONSTRUCTION)
	if c == nil or c.transf == nil then return nil end
	local t = c.transf
	return { num(t[13]), num(t[14]), num(t[15]) }
end

--- Best-effort world position of a station, industry, town building or construction.
function M.positionOf(api, entity, cache)
	if entity == nil or entity < 0 then return nil end
	if cache and cache[entity] ~= nil then return cache[entity] or nil end
	local pos
	pcall(function() pos = transfPos(api, entity) end)
	local scs = api.engine.system.streetConnectorSystem
	for _, fn in ipairs({ "getConstructionEntityForIndustry", "getConstructionEntityForTownBuilding",
		"getConstructionEntityForSubconstruction", "getConstructionEntityForStation" }) do
		if pos then break end
		pcall(function()
			local c = scs[fn](entity)
			if c and c >= 0 then pos = transfPos(api, c) end
		end)
	end
	if pos and pos[1] == nil then pos = nil end
	if cache then cache[entity] = pos or false end
	return pos
end

local function ema(slot, value)
	if slot.n == 0 then slot.v = value else slot.v = slot.v + M.EMA * (value - slot.v) end
	slot.n = slot.n + 1
end

local function calib(s)
	s.calib = s.calib or {}
	s.calib.types = s.calib.types or {}
	s.calib.k = s.calib.k or { v = M.DEFAULT_K, n = 0 }
	return s.calib
end

--- Learns the price factor of a cargo type from one real delivery (see ticket_probe cargoDetail).
function M.learnDelivery(s, detail)
	local segs = detail and detail.segments
	if not segs or #segs == 0 or not segs[1].pos or not detail.unloadPos or not detail.basePrice then return end
	local direct = M.dist(segs[1].pos, detail.unloadPos)
	if direct < 50 then return end
	local c = calib(s)
	local key = tostring(detail.cargoType)
	c.types[key] = c.types[key] or { v = 0, n = 0 }
	ema(c.types[key], detail.basePrice / direct)
end

--- Learns the booked/price ratio from a period where passenger-only lines were observed.
function M.learnRatio(s, journal, price)
	if not journal or not price or price <= 0 then return end
	local r = journal / price
	if r < 0.3 or r > 2 then return end
	ema(calib(s).k, r)
end

local function factorFor(c, cargoType)
	local t = c.types[tostring(cargoType)]
	if t and t.n > 0 then return t.v, true end
	local sum, n = 0, 0
	for _, x in pairs(c.types) do
		if x.n > 0 then sum, n = sum + x.v, n + 1 end
	end
	if n > 0 then return sum / n, false end
	return M.DEFAULT_FACTOR, false
end

-- Where the item was unloaded after its last completed segment, when it waits for its next line.
local function waitingPos(api, sim, cache)
	local C = api.type.ComponentType
	local pos
	pcall(function()
		local w = api.engine.getComponent(sim, C.SIM_ENTITY_AT_TERMINAL)
		if not w then return end
		local line = api.engine.getComponent(w.line, C.LINE)
		local stop = line.stops[w.lineStop0 + 1]
		local group = api.engine.getComponent(stop.stationGroup, C.STATION_GROUP)
		pos = M.positionOf(api, group.stations[stop.station + 1], cache)
	end)
	return pos
end

--- Estimates the pending income of every line.
-- @return { t, lines = { [lineId] = { done, inProgress, units } }, items, skipped, factorsUsed }
function M.compute(api, s, now)
	local C = api.type.ComponentType
	local c = calib(s)
	local k = c.k.v
	local cache = {}
	local lines = {}
	local items, skipped, defaulted = 0, 0, 0

	local function add(line, field, value)
		local key = tostring(line)
		local L = lines[key]
		if L == nil then
			L = { done = 0, inProgress = 0, units = 0 }
			lines[key] = L
		end
		L[field] = L[field] + value
		return L
	end

	local entities = api.engine.getEntitiesWithComponent(C.SIM_CARGO)
	for i = 1, #entities do
		local sim = entities[i]
		local ok = pcall(function()
			local cargo = api.engine.getComponent(sim, C.SIM_CARGO)
			local pps = cargo and cargo.pickupPoints
			local n = pps and #pps or 0
			if n == 0 then return end -- never transported yet

			local pts = {}
			for j = 1, n do pts[j] = vec(pps[j].position) end
			local target = M.positionOf(api, cargo.targetOrPickupEntity, cache)
			if not pts[1] or not target then
				skipped = skipped + 1
				return
			end

			local atVehicle = api.engine.getComponent(sim, C.SIM_ENTITY_AT_VEHICLE)
			local current, inVehicle
			if atVehicle then
				current = vec(api.engine.util.vehicle.getPosition(atVehicle.vehicle))
				inVehicle = true
			else
				current = waitingPos(api, sim, cache)
			end
			if not current then
				skipped = skipped + 1
				return
			end

			-- straight lengths of the segments already driven (the last one up to `current`)
			local lengths, total = {}, 0
			for j = 1, n do
				local b = (j < n) and pts[j + 1] or current
				lengths[j] = b and M.dist(pts[j], b) or 0
				total = total + lengths[j]
			end
			total = total + M.dist(current, target) -- what is still to come, as the crow flies
			if total <= 0 then return end

			local f, known = factorFor(c, cargo.cargoType)
			if not known then defaulted = defaulted + 1 end
			local price = f * M.dist(pts[1], target) * k

			for j = 1, n do
				local share = price * lengths[j] / total
				local field = (inVehicle and j == n) and "inProgress" or "done"
				local L = add(pps[j].line, field, share)
				if j == n then L.units = L.units + 1 end
			end
			items = items + 1
		end)
		if not ok then skipped = skipped + 1 end
	end

	return { t = now, lines = lines, items = items, skipped = skipped, defaultFactor = defaulted, k = k }
end

return M
