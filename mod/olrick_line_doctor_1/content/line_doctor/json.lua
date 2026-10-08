-- Minimal JSON encoder (no game API used, unit-testable with a stock Lua interpreter).
--
-- Tables whose keys are exactly 1..n are encoded as arrays, everything else as objects
-- (non-string keys are converted with tostring). NaN / +-inf become null.
-- Output is deterministic: object keys are sorted.
--
-- Written for speed (the monthly export runs on a simulation thread, measured 160-220 ms for 0.9 MB with the
-- first version): every piece goes into one buffer joined once at the end, instead of building a string per
-- table and concatenating it again at each level.

local M = {}

local ESCAPES = {
	['"'] = '\\"', ["\\"] = "\\\\", ["\b"] = "\\b", ["\f"] = "\\f",
	["\n"] = "\\n", ["\r"] = "\\r", ["\t"] = "\\t",
}

local function escapeChar(c)
	return ESCAPES[c] or string.format("\\u%04x", c:byte())
end

local function escapeString(s)
	if s:find('[%c"\\]') then s = s:gsub('[%c"\\]', escapeChar) end
	return '"' .. s .. '"'
end

local mathType = math.type -- Lua 5.3+; nil on 5.1/5.2
local format = string.format

local function encodeNumber(v)
	if mathType and mathType(v) == "integer" then return tostring(v) end
	if v ~= v or v == math.huge or v == -math.huge then return "null" end
	if v % 1 == 0 and v > -1e15 and v < 1e15 then
		return format("%d", v)
	end
	return format("%.6g", v)
end

local function compareKeys(a, b)
	local ta, tb = type(a), type(b)
	if ta == tb and (ta == "string" or ta == "number") then return a < b end
	return tostring(a) < tostring(b)
end

local encodeValue

-- object keys repeat in every line, vehicle and stock: escape each one once per encode
local keyCache
local function encodeKey(k)
	local e = keyCache[k]
	if e == nil then
		e = escapeString(tostring(k))
		keyCache[k] = e
	end
	return e
end

-- appends the encoding of table t to buffer buf (n = current length), returns the new length
local function encodeTable(t, buf, n, indent, depth, seen)
	if seen[t] then n = n + 1; buf[n] = '"<cycle>"'; return n end
	if depth > 20 then n = n + 1; buf[n] = '"<too deep>"'; return n end

	-- array: keys exactly 1..count
	local count, array = 0, true
	for k in pairs(t) do
		count = count + 1
		if array and (type(k) ~= "number" or k < 1 or k % 1 ~= 0) then array = false end
	end
	if array then
		for i = 1, count do
			if t[i] == nil then array = false; break end
		end
	end
	if count == 0 then n = n + 1; buf[n] = "[]"; return n end

	seen[t] = true
	local pad, padIn, colon = "", "", ":"
	if indent then
		pad = "\n" .. string.rep(indent, depth)
		padIn = "\n" .. string.rep(indent, depth + 1)
		colon = ": "
	end

	if array then
		n = n + 1; buf[n] = "["
		for i = 1, count do
			if i > 1 then n = n + 1; buf[n] = "," end
			if indent then n = n + 1; buf[n] = padIn end
			n = encodeValue(t[i], buf, n, indent, depth + 1, seen)
		end
		if indent then n = n + 1; buf[n] = pad end
		n = n + 1; buf[n] = "]"
	else
		local keys, nk = {}, 0
		for k in pairs(t) do nk = nk + 1; keys[nk] = k end
		table.sort(keys, compareKeys)
		n = n + 1; buf[n] = "{"
		for i = 1, nk do
			local k = keys[i]
			if i > 1 then n = n + 1; buf[n] = "," end
			if indent then n = n + 1; buf[n] = padIn end
			n = n + 1; buf[n] = encodeKey(k)
			n = n + 1; buf[n] = colon
			n = encodeValue(t[k], buf, n, indent, depth + 1, seen)
		end
		if indent then n = n + 1; buf[n] = pad end
		n = n + 1; buf[n] = "}"
	end
	seen[t] = nil
	return n
end

encodeValue = function(v, buf, n, indent, depth, seen)
	local tv = type(v)
	local piece
	if tv == "table" then return encodeTable(v, buf, n, indent, depth, seen)
	elseif tv == "string" then piece = escapeString(v)
	elseif tv == "number" then piece = encodeNumber(v)
	elseif tv == "boolean" then piece = v and "true" or "false"
	elseif tv == "nil" then piece = "null"
	else piece = escapeString(tostring(v)) -- userdata, functions...: keep a readable trace instead of failing
	end
	n = n + 1
	buf[n] = piece
	return n
end

--- Encodes a Lua value as JSON.
-- @param value the value
-- @param indent optional indentation string (e.g. "  ") for pretty output
function M.encode(value, indent)
	local buf = {}
	keyCache = {}
	encodeValue(value, buf, 0, indent, 0, {})
	keyCache = nil
	return table.concat(buf)
end

return M
