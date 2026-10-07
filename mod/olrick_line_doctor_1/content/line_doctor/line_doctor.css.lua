-- Line Doctor styles: the compass sits over the 3D view at the top left, so it gets a dark background.
local ssu = require "::/gui/main/stylesheetutil.lua"

function data()
	local result = {}
	local a = ssu.makeAdder(result)
	local colorDefault = api.gui.genericRep.get(api.gui.genericRep.find("::/gui/main/default_colors.gres")).data

	a("R::LineDoctorCompass !line-doctor-compass", {
		backgroundColor = colorDefault.BaseDark,
		color = colorDefault.NeutralLightest,
		padding = { 6, 12, 6, 12 },
		margin = { 8, 8, 8, 8 },
	})

	return result
end
