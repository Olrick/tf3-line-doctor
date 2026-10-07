-- Line Doctor in-game GUI (read-only).
--
-- Two plugins, registered by the .res.lua files next to this script:
--   * LineDoctorLineCard : a "Line Doctor" card in every line window (LineEowExtensionPoint), built like the
--     game's own line cards;
--   * LineDoctorCompass  : a compass in the bottom game bar, before "Earnings" (GameBarInfoDisplayExtension).
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
		children = { text(label .. " : "), text(value, class) },
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
			row("Profit en attente (segments terminés)", money(s.done), "positive"),
			row("Profit en cours (segment en route)", money(s.inProgress)),
			row("Marchandises en route après cette ligne", tostring(s.units)),
			text("Versé à la livraison au client final. " .. (dateText(s.t) and ("Calculé le " .. dateText(s.t) .. ".")
				or "Pas encore calculé : premier calcul au prochain mois de jeu.")),
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
		if api.gui == nil or api.gui.camera == nil then error("api.gui.camera indisponible ici") end
		local cam = api.gui.camera.getCameraData()
		if cam == nil then error("getCameraData() renvoie nil") end
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
		if angle == nil then error("angle introuvable dans " .. tostring(cam)) end
		local raw = math.deg(angle)
		out.raw = raw
		out.heading = (COMPASS_OFFSET_DEG + COMPASS_SIGN * raw) % 360
	end)
	if not ok then out.err = tostring(err) end
	return out
end

local LineDoctorCompass = react.RegisterPluginRecipe(game_bar_widgets.GameBarInfoDisplayExtension, "LineDoctorCompass", function()
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
	local label, tip = "?", "Boussole Line Doctor : " .. tostring(s.err or "orientation indisponible")
	if s.heading then
		label = DIRECTIONS[math.floor((s.heading + 22.5) / 45) % 8 + 1]
		tip = string.format("Boussole Line Doctor : cap %d° (angle brut de la caméra %.0f°). N = axe y de la carte.",
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
						builtin.TextView{ meta = { class = "font-scale-headline" }, text = "Cap" },
						builtin.TextView{ meta = { class = "font-scale-headline" }, text = label },
						builtin.TextView{ meta = { class = "font-scale-body" },
							text = s.heading and string.format("%d°", math.floor(s.heading + 0.5)) or "" },
					},
				},
			},
		},
	}
end)

function data()
	return {
		LineDoctorLineCard = LineDoctorLineCard,
		LineDoctorCompass = LineDoctorCompass,
	}
end
