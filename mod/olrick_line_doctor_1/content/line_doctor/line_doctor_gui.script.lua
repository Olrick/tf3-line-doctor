-- Line Doctor in-game GUI (read-only).
--
-- One plugin, registered by line_doctor_card.res.lua: a "Line Doctor" card in every line window
-- (LineEowExtensionPoint), built like the game's own line cards.
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
	if t == nil then return "pas encore calculé" end
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
			text("Versé à la livraison au client final. Calculé le " .. dateText(s.t) .. "."),
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

function data()
	return {
		LineDoctorLineCard = LineDoctorLineCard,
	}
end
