-- Line Doctor in-game GUI (read-only).
--
-- Three plugins, registered by the .res.lua files next to this script:
--   * LineDoctorLineCard   : a "Line Doctor" card in every line window (LineEowExtensionPoint)
--   * LineDoctorButton     : a "Line Doctor" button in the mods button area (MainModButtonAreaExtension)
--   * LineDoctorWindowHost : the window opened by that button, one row per line (ModEntryPointExtension)
--
-- The pending income is computed by the game script (pending.lua) and read from its state, so the GUI only
-- displays numbers and never touches the simulation.

local react = ug_require "::/gui/main/react.lua"
local builtin = ug_require "::/gui/main/builtin.lua"
local content_card = ug_require "::/gui/main/content_card.tl"
local engine_react_util = ug_require "::/gui/main/engine_react_util.tl"
local line_eow = ug_require "::/gui/entity_window/line/line_eow.script.tl"
local main_mod_button_area = ug_require "::/gui/main/main_mod_button_area.tl"
local mod_entry_point = ug_require "::/gui/main/mod_entry_point.tl"

local GAME_SCRIPT = "olrick_line_doctor_1::/line_doctor/line_doctor.gs"
local TOGGLE_EVENT = "lineDoctorToggleWindow"
local WINDOW_ID = "line_doctor.window"

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

local function balance12m(line)
	local value
	pcall(function()
		local gt = api.engine.getComponent(api.engine.util.getWorld(), api.type.ComponentType.GAME_TIME)
		local to = gt.gameTime
		value = api.engine.util.finance.calculateBalance({ line }, math.max(to - api.util.getDefaultYearDuration(), 0), to, true)
	end)
	return value
end

local function lineName(line)
	local name = tostring(line)
	pcall(function() name = api.engine.util.getEntityName(line) end)
	return name
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

-- ---------------------------------------------------------------------------
-- button + window with all lines
-- ---------------------------------------------------------------------------

local LineDoctorButton = react.RegisterPluginRecipe(main_mod_button_area.MainModButtonAreaExtension, "LineDoctorButton", function()
	return builtin.Button{
		meta = { tooltip = "Line Doctor : profit en attente par ligne" },
		content = text("Line Doctor"),
		onClick = function()
			react.fireEvent(nil, TOGGLE_EVENT)
		end,
	}
end)

local function cell(field, formatter, class)
	return react.RegisterRecipe("LineDoctorCell_" .. field, function(params)
		local value = params.userParam.rows[params.rowKey] and params.userParam.rows[params.rowKey][field]
		react.setStyleClasses("right-aligned")
		return text(formatter(value), class)
	end)
end

local NameCell = react.RegisterRecipe("LineDoctorCell_name", function(params)
	local r = params.userParam.rows[params.rowKey]
	return text(r and r.name or tostring(params.rowKey))
end)
local DoneCell = cell("done", money, "positive")
local InProgressCell = cell("inProgress", money)
local UnitsCell = cell("units", function(v) return tostring(v or 0) end)
local BalanceCell = cell("balance", money)

local LineDoctorTable = react.RegisterRecipe("LineDoctorTable", function()
	local state = engine_react_util.useStepStateTimer(function()
		local p = readPending()
		local rows, keys = {}, {}
		local lines = {}
		pcall(function() lines = api.engine.system.lineSystem.getLinesForPlayer(api.engine.util.getPlayer()) end)
		for i = 1, #lines do
			local line = lines[i]
			local L = p.lines[tostring(line)] or {}
			rows[line] = {
				name = lineName(line), done = L.done or 0, inProgress = L.inProgress or 0,
				units = L.units or 0, balance = balance12m(line),
			}
			keys[#keys + 1] = line
		end
		return { rows = rows, keys = keys, t = p.t, items = p.items }
	end, 5.0)
	local s = state:old()
	local function by(field) return function(line) return s.rows[line] and s.rows[line][field] or 0 end end

	return builtin.BoxLayout{
		orientation = builtin.type.Orientation.Vertical,
		children = {
			text(string.format("Calculé le %s sur %s marchandises en route. Passagers : payés à chaque trajet, jamais en attente.",
				dateText(s.t), tostring(s.items or 0))),
			builtin.DataTable{
				meta = { id = "line_doctor.table" },
				columns = {
					builtin.ColumnDesc{ name = "Ligne", recipe = NameCell, getCompareValue = by("name"), weight = 4 },
					builtin.ColumnDesc{ name = "Résultat 12 mois", recipe = BalanceCell, getCompareValue = by("balance"), weight = 2, headerStyleClass = "right-aligned" },
					builtin.ColumnDesc{ name = "En attente", recipe = DoneCell, getCompareValue = by("done"), weight = 2, headerStyleClass = "right-aligned" },
					builtin.ColumnDesc{ name = "En cours", recipe = InProgressCell, getCompareValue = by("inProgress"), weight = 2, headerStyleClass = "right-aligned" },
					builtin.ColumnDesc{ name = "Unités", recipe = UnitsCell, getCompareValue = by("units"), weight = 1, headerStyleClass = "right-aligned" },
				},
				rowKeys = s.keys,
				userParam = { rows = s.rows },
			},
		},
	}
end)

local LineDoctorWindowHost = react.RegisterPluginRecipe(mod_entry_point.ModEntryPointExtension, "LineDoctorWindowHost", function()
	local visible = react.useState(false)
	react.onEvent(TOGGLE_EVENT, function()
		visible:set(not visible:old())
	end)
	if not visible:old() then
		return nil
	end
	return builtin.Window{
		id = WINDOW_ID,
		title = "Line Doctor — profit en attente par ligne",
		closable = true,
		onClose = function() visible:set(false) end,
		content = LineDoctorTable{},
	}
end)

function data()
	return {
		LineDoctorLineCard = LineDoctorLineCard,
		LineDoctorButton = LineDoctorButton,
		LineDoctorWindowHost = LineDoctorWindowHost,
	}
end
