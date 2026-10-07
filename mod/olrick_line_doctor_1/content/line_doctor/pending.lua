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
M.DEFAULT_K = 1.0       -- booked income / computed price when the game's multiplier cannot be read
M.EMA = 0.1             -- weight of a new measurement
M.MAX_CHAINS = 400      -- distinct delivered chains remembered (cargo type + sequence of lines)
M.MAX_PREFIXES = 600    -- distinct partial chains reported per computation
M.MAX_POSITIONS = 4000  -- remembered destination / stop positions

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
	s.calib.k = { v = M.DEFAULT_K, n = 0 }
	return s.calib
end

--- Chain key: cargo type and the sequence of lines, e.g. "12|1734>2207".
function M.chainKey(cargoType, lineIds)
	local parts = {}
	for i, l in ipairs(lineIds) do parts[i] = tostring(l) end
	return tostring(cargoType) .. "|" .. table.concat(parts, ">")
end

-- Remembers which complete chains really deliver (cumulated since the mod runs on the save).
local function recordChain(s, detail)
	local lines = {}
	for i, seg in ipairs(detail.segments) do lines[i] = seg.line end
	local key = M.chainKey(detail.cargoType, lines)
	s.chains = s.chains or {}
	local c = s.chains[key]
	if c == nil then
		local count = 0
		for _ in pairs(s.chains) do count = count + 1 end
		if count >= M.MAX_CHAINS then return end
		c = { cargoType = detail.cargoType, lines = lines, n = 0, price = 0, first = detail.t }
		s.chains[key] = c
	end
	c.n = c.n + 1
	c.price = c.price + (detail.basePrice or 0)
	c.last = detail.t

	-- final customers and producers of this chain (a few each, with counts): they link chains into
	-- production trees (a chain starting at an industry is fed by the chains delivering to it)
	local function count(field, entity)
		if entity == nil then return end
		c[field] = c[field] or {}
		local key = tostring(entity)
		if c[field][key] ~= nil then
			c[field][key] = c[field][key] + 1
		else
			local n = 0
			for _ in pairs(c[field]) do n = n + 1 end
			if n < 5 then c[field][key] = 1 end
		end
	end
	count("targets", detail.target)
	count("sources", detail.source)

	-- share of the price earned by each step (straight segment lengths, as the game shares it)
	local pts = {}
	for i, seg in ipairs(detail.segments) do pts[i] = seg.pos end
	pts[#pts + 1] = detail.unloadPos
	local lengths, total, complete = {}, 0, true
	for i = 1, #detail.segments do
		if pts[i] == nil or pts[i + 1] == nil then complete = false break end
		lengths[i] = M.dist(pts[i], pts[i + 1])
		total = total + lengths[i]
	end
	if complete and total > 0 then
		c.stepShare = c.stepShare or {}
		for i = 1, #lengths do c.stepShare[i] = (c.stepShare[i] or 0) + lengths[i] / total end
		c.nShare = (c.nShare or 0) + 1
	end
end

--- Display names of the final customers of the recorded chains (industry, or the town of a building).
function M.resolveTargetNames(api, s)
	s.targetNames = s.targetNames or {}
	local C = api.type.ComponentType
	local scs = api.engine.system.streetConnectorSystem
	local keys = {}
	for _, c in pairs(s.chains or {}) do
		for k in pairs(c.targets or {}) do keys[k] = true end
		for k in pairs(c.sources or {}) do keys[k] = true end
	end
	do
		for tkey in pairs(keys) do
			if s.targetNames[tkey] == nil then
				local target = tonumber(tkey)
				local name
				pcall(function()
					local tb = api.engine.getComponent(target, C.TOWN_BUILDING)
					if tb and tb.town then name = "Ville de " .. api.engine.util.getEntityName(tb.town) end
				end)
				for _, fn in ipairs({ "getConstructionEntityForIndustry", "getConstructionEntityForSubconstruction" }) do
					if name then break end
					pcall(function()
						local con = scs[fn](target)
						if con and con >= 0 then
							local n = api.engine.util.getEntityName(con)
							if n and n ~= "" then name = n end
						end
					end)
				end
				if not name then
					pcall(function()
						local n = api.engine.util.getEntityName(target)
						if n and n ~= "" then name = n end
					end)
				end
				s.targetNames[tkey] = name or false
			end
		end
	end
end

--- Learns from one real delivery (see ticket_probe cargoDetail): price factor of the cargo type and the
--- chain of lines it used.
local function remember(s, field, key, pos)
	if key == nil or pos == nil then return end
	s[field] = s[field] or {}
	local t = s[field]
	key = tostring(key)
	if t[key] == nil then
		s[field .. "Count"] = (s[field .. "Count"] or 0) + 1
		if s[field .. "Count"] > M.MAX_POSITIONS then return end
	end
	t[key] = pos
end

function M.learnDelivery(s, detail)
	local segs = detail and detail.segments
	if not segs or #segs == 0 then return end
	pcall(recordChain, s, detail)
	remember(s, "targetPos", detail.target, detail.unloadPos) -- where cargo for this customer gets unloaded
	if not segs[1].pos or not detail.unloadPos or not detail.basePrice then return end
	local direct = M.dist(segs[1].pos, detail.unloadPos)
	if direct < 50 then return end
	local c = calib(s)
	local key = tostring(detail.cargoType)
	c.types[key] = c.types[key] or { v = 0, n = 0 }
	ema(c.types[key], detail.basePrice / direct)
end

--- Learns the booked/price ratio from a period where passenger-only lines were observed.
-- Disabled: measured over periods spanning save reloads it drifted (1.37 instead of 0.804); the constant
-- measured on 1 335 events is used instead. Kept so callers stay valid.
function M.learnRatio(_s, _journal, _price)
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

-- Positions of the stops of every player line, from the vehicles standing at them (remembered).
function M.learnStopPositions(api, s)
	local C = api.type.ComponentType
	local lines = api.engine.system.lineSystem.getLinesForPlayer(api.engine.util.getPlayer())
	for li = 1, #lines do
		pcall(function()
			local vehicles = api.engine.system.transportVehicleSystem.getLineVehicles(lines[li])
			for vi = 1, #vehicles do
				local tv = api.engine.getComponent(vehicles[vi], C.TRANSPORT_VEHICLE)
				local atTerminal = tv and (tostring(tv.state) == "2" or tostring(tv.state) == "AT_TERMINAL")
				if atTerminal then
					remember(s, "stopPos", tostring(lines[li]) .. ":" .. tostring(tv.stopIndex),
						vec(api.engine.util.vehicle.getPosition(vehicles[vi])))
				end
			end
		end)
	end
end

-- Where the item was unloaded after its last completed segment, when it waits for its next line.
local function waitingPos(api, s, sim, cache)
	local C = api.type.ComponentType
	local pos
	pcall(function()
		local w = api.engine.getComponent(sim, C.SIM_ENTITY_AT_TERMINAL)
		if not w then return end
		pos = s.stopPos and s.stopPos[tostring(w.line) .. ":" .. tostring(w.lineStop0)]
		if pos then return end
		local line = api.engine.getComponent(w.line, C.LINE)
		local stop = line.stops[w.lineStop0 + 1]
		local group = api.engine.getComponent(stop.stationGroup, C.STATION_GROUP)
		pos = M.positionOf(api, group.stations[stop.station + 1], cache)
	end)
	return pos
end

-- Cargo items that already used a line: on board a vehicle, or waiting at a line stop for their next line.
-- (The engine refuses to iterate all SIM_CARGO entities: "Cannot loop over this component type".)
function M.gatherSims(api)
	local sys = api.engine.system
	local seen, list = {}, {}
	local function push(sim)
		if sim ~= nil and not seen[sim] then
			seen[sim] = true
			list[#list + 1] = sim
		end
	end
	pcall(function()
		for _, byCargo in pairs(sys.simEntityAtVehicleSystem.getVehicle2Cargo2SimEntitesMap()) do
			for cargoType, sims in pairs(byCargo) do
				if num(cargoType) ~= 0 then -- passengers are paid per ride
					for i = 1, #sims do push(sims[i]) end
				end
			end
		end
	end)
	pcall(function()
		local lines = sys.lineSystem.getLinesForPlayer(api.engine.util.getPlayer())
		for li = 1, #lines do
			local line = lines[li]
			pcall(function()
				local comp = api.engine.getComponent(line, api.type.ComponentType.LINE)
				local usages = api.engine.util.line.getLineCapacityUsages(line, true)
				for index = 2, #usages do -- entry i = cargo type i - 1; entry 1 = passengers
					local u = usages[index]
					if u and ((num(u.capacity) or 0) > 0) then
						for stop = 0, #comp.stops - 1 do
							local sims = sys.simEntityAtTerminalSystem.getLineStopSimEntities(line, stop, index - 1)
							for i = 1, #sims do push(sims[i]) end
						end
					end
				end
			end)
		end
	end)
	return list
end

--- The company's ticket price multiplier (the "inflation" of TF3: 1 - (rank - 1) x (1 - floor) / 14), as
--- stored by the game's company progression script; nil multiplier = no reduction. Measured 0.804 at rank ~12
--- with "Normal" inflation, 1.00 once inflation was set to "None" (2026-10-07).
function M.priceMultiplier(api)
	local k
	pcall(function()
		local e = api.engine.system.gameScriptSystem.getEntityForGameScript("::game_mechanics/company/company_progression.gs")
		local gs = api.engine.getComponent(e, api.type.ComponentType.GAME_SCRIPT)
		local data = gs.state.companyState[api.engine.util.getPlayer()]
		k = num(data and data.ticketPriceMultiplier) or 1.0
	end)
	return k
end

--- Estimates the pending income of every line.
-- @return { t, lines = { [lineId] = { done, inProgress, units } }, items, skipped, factorsUsed }
function M.compute(api, s, now)
	local C = api.type.ComponentType
	local c = calib(s)
	local k = M.priceMultiplier(api) or c.k.v
	local cache = {}
	local lines = {}
	local prefixes, prefixCount = {}, 0
	local items, skipped, defaulted = 0, 0, 0
	local why = { noFirstPos = 0, noTarget = 0, noVehiclePos = 0, noWaitingPos = 0, error = 0 }
	pcall(M.learnStopPositions, api, s)

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

	local entities = M.gatherSims(api)
	for i = 1, #entities do
		local sim = entities[i]
		local ok, err = pcall(function()
			local cargo = api.engine.getComponent(sim, C.SIM_CARGO)
			local pps = cargo and cargo.pickupPoints
			local n = pps and #pps or 0
			if n == 0 then return end -- never transported yet

			local pts = {}
			for j = 1, n do pts[j] = vec(pps[j].position) end
			local target = s.targetPos and s.targetPos[tostring(cargo.targetOrPickupEntity)]
			if not target then target = M.positionOf(api, cargo.targetOrPickupEntity, cache) end
			if not pts[1] or not target then
				skipped = skipped + 1
				if not pts[1] then why.noFirstPos = why.noFirstPos + 1 else why.noTarget = why.noTarget + 1 end
				return
			end

			local atVehicle = api.engine.getComponent(sim, C.SIM_ENTITY_AT_VEHICLE)
			local current, inVehicle
			if atVehicle then
				current = vec(api.engine.util.vehicle.getPosition(atVehicle.vehicle))
				inVehicle = true
			else
				current = waitingPos(api, s, sim, cache)
			end
			if not current then
				skipped = skipped + 1
				if inVehicle then why.noVehiclePos = why.noVehiclePos + 1 else why.noWaitingPos = why.noWaitingPos + 1 end
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

			-- partial chain this item is on (lines done so far), for the end-to-end view
			local seq = {}
			for j = 1, n do seq[j] = pps[j].line end
			local pkey = M.chainKey(cargo.cargoType, seq) .. (inVehicle and "~" or "")
			local P = prefixes[pkey]
			if P == nil and prefixCount < M.MAX_PREFIXES then
				P = { cargoType = cargo.cargoType, lines = seq, inVehicle = inVehicle or false, units = 0,
					done = {}, inProgress = 0, price = 0 }
				for j = 1, n do P.done[j] = 0 end
				prefixes[pkey] = P
				prefixCount = prefixCount + 1
			end

			for j = 1, n do
				local share = price * lengths[j] / total
				local field = (inVehicle and j == n) and "inProgress" or "done"
				local L = add(pps[j].line, field, share)
				if j == n then L.units = L.units + 1 end
				if P then
					if field == "done" then P.done[j] = P.done[j] + share else P.inProgress = P.inProgress + share end
				end
			end
			if P then
				P.units = P.units + 1
				P.price = P.price + price
			end
			items = items + 1
		end)
		if not ok then
			skipped = skipped + 1
			why.error = why.error + 1
			why.firstError = why.firstError or tostring(err)
		end
	end

	local prefixList = {}
	for _, P in pairs(prefixes) do prefixList[#prefixList + 1] = P end
	pcall(M.resolveTargetNames, api, s)
	local known = { targets = s.targetPosCount or 0, stops = s.stopPosCount or 0 }
	return { t = now, lines = lines, items = items, skipped = skipped, skippedWhy = why, positionsKnown = known,
		defaultFactor = defaulted, k = k,
		prefixes = prefixList, chains = s.chains, targetNames = s.targetNames }
end

return M
