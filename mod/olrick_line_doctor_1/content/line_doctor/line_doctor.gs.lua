-- Game script: exports a snapshot of all lines for the Line Doctor analyzer.
-- (Registered by its .gs.lua file ending.)
function data()
	return {
		updateScript = {
			fileName = "line_doctor.script@update",
		},
		handleEventScript = {
			fileName = "line_doctor.script@handleEvent",
		},
	}
end
