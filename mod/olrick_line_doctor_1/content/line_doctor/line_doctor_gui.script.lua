-- Line Doctor in-game GUI (read-only).
--
-- Two plugins, registered by the .res.lua files next to this script:
--   * LineDoctorLineCard : a "Line Doctor" card in every line window (LineEowExtensionPoint), built like the
--     game's own line cards;
--   * LineDoctorCompass  : a compass and the company health in the bottom game bar, before "Earnings"
--     (GameBarInfoDisplayExtension).
--     There is no visible extension point at the top of the screen: ModEntryPointExtension is mounted in an
--     "internal-hidden" layer with class "invisible" (game.tl), for logic hooks only.
--
-- No mod button/window: plugin recipes must return a layout, and game windows are opened through the
-- window container of gameCtx, which the mod button area does not provide (crash "Recipe child must be a
-- layout" on 2026-10-06). The all-lines / end-to-end view is analyzer/chains.py (HTML, outside the game).
--
-- The pending income is computed by the game script (pending.lua) and read from its state, so the GUI only
-- displays numbers and never touches the simulation.

local react = ug_require "::/gui/main/react.lua"
local builtin = ug_require "::/gui/main/builtin.lua"
local content_card = ug_require "::/gui/main/content_card.tl"
local engine_react_util = ug_require "::/gui/main/engine_react_util.tl"
local line_eow = ug_require "::/gui/entity_window/line/line_eow.script.tl"
local game_bar_widgets = ug_require "::/gui/game_bar/game_bar_widgets.tl"

local GAME_SCRIPT = "olrick_line_doctor_1::/line_doctor/line_doctor.gs"

-- Translation: in the game `_` looks the text up in the mod's strings.json for the game language (the English
-- text is the key, so a missing translation shows English). Called at display time, from a file-level
-- function so that loop variables named `_` never shadow it.
local function tr(text)
	if type(_) == "function" then
		local ok, translated = pcall(_, text)
		if ok and type(translated) == "string" and translated ~= "" then return translated end
	end
	return text
end

-- ---------------------------------------------------------------------------
-- data
-- ---------------------------------------------------------------------------

local function readPending()
	local result = { lines = {}, t = nil }
	pcall(function()
		local entity = api.engine.system.gameScriptSystem.getEntityForGameScript(GAME_SCRIPT)
		local gs = api.engine.getComponent(entity, api.type.ComponentType.GAME_SCRIPT)
		local p = gs and gs.state and gs.state.pending
		if p then
			result.lines = p.lines or {}
			result.t = p.t
			result.items = p.items
		end
	end)
	return result
end

-- company health computed by the game script at each export (collector.health), nil before the first one
local function readHealth()
	local h
	pcall(function()
		local entity = api.engine.system.gameScriptSystem.getEntityForGameScript(GAME_SCRIPT)
		local gs = api.engine.getComponent(entity, api.type.ComponentType.GAME_SCRIPT)
		h = gs and gs.state and gs.state.health
	end)
	return h
end

local function money(v)
	if v == nil then return "–" end
	local ok, text = pcall(api.util.formatMoney, math.floor(v + 0.5))
	if ok then return text end
	return tostring(math.floor(v + 0.5))
end

local function dateText(t)
	if t == nil then return nil end
	local text = "?"
	pcall(function()
		local d = api.engine.util.getCalendarDate(t)
		text = string.format("%02d/%02d/%d", d.day, d.month, d.year)
	end)
	return text
end

local function text(value, class)
	return builtin.TextView{ meta = class and { class = class } or nil, text = value }
end

local function row(label, value, class)
	return builtin.BoxLayout{
		orientation = builtin.type.Orientation.Horizontal,
		children = { text(label .. tr(": ")), text(value, class) },
	}
end

-- ---------------------------------------------------------------------------
-- card in the line window
-- ---------------------------------------------------------------------------

local LineDoctorCardContent = react.RegisterRecipe("LineDoctorCardContent", function(params)
	local state = engine_react_util.useStepStateTimer(function()
		local p = readPending()
		local L = p.lines[tostring(params.entityId)] or {}
		return { done = L.done or 0, inProgress = L.inProgress or 0, units = L.units or 0, t = p.t }
	end, 2.0)
	local s = state:old()
	return builtin.BoxLayout{
		orientation = builtin.type.Orientation.Vertical,
		children = {
			row(tr("Pending profit (completed legs)"), money(s.done), "positive"),
			row(tr("Profit in progress (leg on its way)"), money(s.inProgress)),
			row(tr("Cargo on its way after this line"), tostring(s.units)),
			text(tr("Paid on delivery to the final customer.") .. " " .. (dateText(s.t)
				and string.format(tr("Computed on %s."), dateText(s.t))
				or tr("Not computed yet: first computation at the next game month."))),
		},
	}
end)

local LineDoctorLineCard = react.RegisterPluginRecipe(line_eow.LineEowExtensionPoint, "LineDoctorLineCard", function(params)
	return builtin.BoxLayout{
		child = content_card.ContentCard{
			meta = { localKey = "lineDoctorPending" },
			title = "Line Doctor",
			initialCalloutTextPermanent = "",
			recipeAndParamPermanent = content_card.makeRecipeAndParam(LineDoctorCardContent, { entityId = params.entityId }),
			gameCtx = params.gameCtx,
			showOnRightSide = params.showCalloutOnRightSide,
		}
	}
end)

-- ---------------------------------------------------------------------------
-- compass in the top game bar
-- ---------------------------------------------------------------------------

-- The game has no official north: "N" is the map's +y axis, the convention of the Line Doctor reports.
-- api.gui.camera.getCameraData() = {x, y, distance, angle, pitch}; the zero and direction of `angle` are not
-- documented: heading = OFFSET + SIGN x angle (degrees), calibrated in game (the raw angle is shown for that).
local COMPASS_OFFSET_DEG = 219 -- calibrated 2026-10-07: raw 206° when facing a landmark at map bearing 65°
local COMPASS_SIGN = 1
local DIRECTIONS = { "N", "NE", "E", "SE", "S", "SW", "W", "NW" }

local function compassState()
	local out = { heading = nil, raw = nil, err = nil }
	local ok, err = pcall(function()
		if api.gui == nil or api.gui.camera == nil then error("api.gui.camera unavailable here") end
		local cam = api.gui.camera.getCameraData()
		if cam == nil then error("getCameraData() returned nil") end
		-- Vec5f {x, y, distance, angle, pitch}: the game's tutorial reads the angle as `.w`
		local angle
		for _, read in ipairs({
			function() return cam.w end, function() return cam[4] end, function() return cam.angle end,
		}) do
			if angle == nil then
				local okRead, v = pcall(read)
				if okRead and type(v) == "number" then angle = v end
			end
		end
		if angle == nil then error("no angle in " .. tostring(cam)) end
		local raw = math.deg(angle)
		out.raw = raw
		out.heading = (COMPASS_OFFSET_DEG + COMPASS_SIGN * raw) % 360
	end)
	if not ok then out.err = tostring(err) end
	return out
end

-- ---------------------------------------------------------------------------
-- company health in the game bar: one dot per ratio, green / orange / red
-- ---------------------------------------------------------------------------

-- no-break space (UTF-8 bytes): plain spaces at the edge of a TextView may be trimmed
local NBSP = "\194\160"

-- global classes of gui/main/default.css: success = Ok (green), warning = goldenrod, error = red
-- ("positive" is the game's blue)
local LEVEL_CLASS = { vert = "success", orange = "warning", rouge = "error" }
-- levels and ratio keys come from collector.health (French level names in the saved state)
local LEVEL_NAME = { vert = "green", orange = "orange", rouge = "red", gris = "grey" }
local RATIO_NAME = {
	running = "Income / vehicle running costs",
	buildings = "Building upkeep / income",
	vmaint = "Vehicle maintenance / income",
	margin = "Operating result / income",
}

local function decimal(v, pct)
	if v == nil then return "n/a" end
	local text = pct and string.format("%.0f%%", v * 100) or string.format("%.2f", v)
	local sep = tr("DECIMAL_SEPARATOR")
	if sep == "DECIMAL_SEPARATOR" then sep = "." end
	return (text:gsub("%.", sep))
end

local function healthTooltip(h)
	if h == nil then return tr("Line Doctor health: not computed yet (first computation at the next export).") end
	local out = {
		string.format(tr("Line Doctor health: projected at the max rank (rank %s: prices x %s instead of x %s today, at rank %s)."),
			tostring(h.maxRank or 15), decimal(h.multiplierAtMaxRank), decimal(h.multiplier), tostring(h.rank or "?")),
		string.format(tr("Over the last %d complete months, subsidies excluded."), h.periods or 0)
			.. (dateText(h.t) and (" " .. string.format(tr("Computed on %s."), dateText(h.t))) or ""),
	}
	for _, r in ipairs(h.ratios or {}) do
		local cmpGood, cmpBad = r.higher and ">=" or "<=", r.higher and "<" or ">"
		out[#out + 1] = string.format(tr("[%s] %s: %s (green %s %s, red %s %s)"), tr(LEVEL_NAME[r.level] or "grey"),
			tr(RATIO_NAME[r.key] or r.name or "?"), decimal(r.value, r.pct), cmpGood, decimal(r.good, r.pct), cmpBad,
			decimal(r.bad, r.pct))
	end
	return table.concat(out, "\n")
end

local function healthView(h)
	local children = { builtin.TextView{ meta = { class = "font-scale-headline" }, text = tr("Health") .. NBSP } }
	for _, r in ipairs((h and h.ratios) or {}) do
		children[#children + 1] = builtin.TextView{
			-- classes are comma-separated, like the game's earnings widget ("font-scale-headline, positive")
			meta = { class = "font-scale-headline" .. (LEVEL_CLASS[r.level] and (", " .. LEVEL_CLASS[r.level]) or "") },
			text = "•",
		}
	end
	if h == nil then children[#children + 1] = builtin.TextView{ meta = { class = "font-scale-body" }, text = "…" } end
	return builtin.Component {
		meta = { tooltip = healthTooltip(h) },
		layout = builtin.BoxLayout{ orientation = builtin.type.Orientation.Horizontal, children = children },
	}
end

local LineDoctorCompass = react.RegisterPluginRecipe(game_bar_widgets.GameBarInfoDisplayExtension, "LineDoctorCompass", function()
	-- the health is simulation state: read it in useStepStateTimer, like the line card
	local health = engine_react_util.useStepStateTimer(function() return { h = readHealth() } end, 5.0)
	-- The camera is GUI api: reading it inside useStepStateTimer fails with "api currently restricted" (that
	-- hook reads the simulation). react.onStep runs in the GUI update, like the game's own buttons.
	local state = react.useState({ heading = nil, raw = nil, err = nil })
	react.onStep(function()
		local new = compassState()
		local old = state:old()
		local changed = (new.err ~= old.err) or (new.heading == nil) ~= (old.heading == nil)
			or (new.heading and old.heading and math.abs(new.heading - old.heading) >= 1)
		if changed then state:set(new) end
	end)
	local s = state:old()
	local label, tip = "?", tr("Line Doctor compass: ") .. tostring(s.err or tr("orientation unavailable"))
	if s.heading then
		label = DIRECTIONS[math.floor((s.heading + 22.5) / 45) % 8 + 1]
		tip = string.format(tr("Line Doctor compass: heading %d° (raw camera angle %.0f°). N = map y axis."),
			math.floor(s.heading + 0.5), s.raw)
	end
	return builtin.BoxLayout{
		orientation = builtin.type.Orientation.Horizontal,
		children = {
			builtin.Component {
				meta = { tooltip = tip },
				layout = builtin.BoxLayout{
					orientation = builtin.type.Orientation.Horizontal,
					children = {
						builtin.TextView{ meta = { class = "font-scale-headline" }, text = tr("Heading") },
						builtin.TextView{ meta = { class = "font-scale-headline" }, text = NBSP .. label },
						builtin.TextView{ meta = { class = "font-scale-body" },
							text = s.heading and string.format("%d°", math.floor(s.heading + 0.5)) or "" },
					},
				},
			},
			builtin.TextView{ meta = { class = "font-scale-headline" }, text = NBSP:rep(4) }, -- gap compass / health
			healthView(health:old().h),
		},
	}
end)

function data()
	return {
		LineDoctorLineCard = LineDoctorLineCard,
		LineDoctorCompass = LineDoctorCompass,
	}
end
