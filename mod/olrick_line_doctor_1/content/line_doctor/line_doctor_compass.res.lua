function data()
	return {
		type = "react-plugin ::GameBarInfoDisplayExtension",
		data = {
			filePath = "olrick_line_doctor_1::/line_doctor/line_doctor_gui.script@LineDoctorCompass",
			priority = -10, -- before the game's Earnings (-2) and Transported (-1): the bar scrolls and hides what comes last
		}
	}
end
