-- Minimal JSON encoder (no game API used, unit-testable with a stock Lua interpreter).
--
-- Tables whose keys are exactly 1..n are encoded as arrays, everything else as objects
-- (non-string keys are converted with tostring). NaN / +-inf become null.
-- Output is deterministic: object keys are sorted.

local M = {}

local ESCAPES = {
	['"'] = '\\"', ["\\"] = "\\\\", ["\b"] = "\\b", ["\f"] = "\\f",
	["\n"] = "\\n", ["\r"] = "\\r", ["\t"] = "\\t",
}

local function escapeString(s)
	s = s:gsub('[%c"\\]', function(c)
		return ESCAPES[c] or string.format("\\u%04x", c:byte())
	end)
	return '"' .. s .. '"'
end

local function isArray(t)
	local n = 0
	for k in pairs(t) do
		if type(k) ~= "number" or k < 1 or k % 1 ~= 0 then return false end
		n = n + 1
	end
	for i = 1, n do
		if t[i] == nil then return false end
	end
	return true, n
end

local function encodeNumber(v)
	if v ~= v or v == math.huge or v == -math.huge then return "null" end
	if v % 1 == 0 and v > -1e15 and v < 1e15 then
		return string.format("%d", v)
	end
	return string.format("%.6g", v)
end

local encode

local function encodeTable(t, indent, depth, seen)
	if seen[t] then return '"<cycle>"' end
	if depth > 20 then return '"<too deep>"' end
	seen[t] = true

	local pad, padIn, sep, colon = "", "", ",", ":"
	if indent then
		pad = "\n" .. string.rep(indent, depth)
		padIn = "\n" .. string.rep(indent, depth + 1)
		colon = ": "
	end

	local out = {}
	local array, n = isArray(t)
	local result
	if array then
		if n == 0 then
			seen[t] = nil
			return "[]"
		end
		for i = 1, n do
			out[i] = padIn .. encode(t[i], indent, depth + 1, seen)
		end
		result = "[" .. table.concat(out, sep) .. pad .. "]"
	else
		local keys = {}
		for k in pairs(t) do keys[#keys + 1] = k end
		table.sort(keys, function(a, b) return tostring(a) < tostring(b) end)
		for _, k in ipairs(keys) do
			out[#out + 1] = padIn .. escapeString(tostring(k)) .. colon .. encode(t[k], indent, depth + 1, seen)
		end
		result = "{" .. table.concat(out, sep) .. pad .. "}"
	end
	seen[t] = nil
	return result
end

encode = function(v, indent, depth, seen)
	local tv = type(v)
	if tv == "nil" then return "null" end
	if tv == "boolean" then return v and "true" or "false" end
	if tv == "number" then return encodeNumber(v) end
	if tv == "string" then return escapeString(v) end
	if tv == "table" then return encodeTable(v, indent, depth, seen) end
	-- userdata, functions...: keep a readable trace instead of failing
	return escapeString(tostring(v))
end

--- Encodes a Lua value as JSON.
-- @param value the value
-- @param indent optional indentation string (e.g. "  ") for pretty output
function M.encode(value, indent)
	return encode(value, indent, 0, {})
end

return M
