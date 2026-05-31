import Foundation

final class BridgeConfigTests {
    func testBridgeConfigDecodesFullPayload() throws {
        let json = Self.fullConfigJSON
        let config = try JSONDecoder().decode(BridgeConfig.self, from: Data(json.utf8))
        try expectEqual(config.ftp.mode, .peer)
        try expectEqual(config.ftp.username, "camera")
        try expectTrue(config.ftp.passwordSet)
        try expectEqual(config.printer.model, "mini_link3")
        try expectEqual(config.printer.quality, 80)
        try expectEqual(config.printer.keepaliveIntervalSeconds, 30)
        try expectNil(config.workflow.autoPrintDelaySeconds)
        try expectTrue(config.workflow.allowPrintWithoutFilm)
        try expectTrue(config.power.idlePoweroffEnabled)
        try expectEqual(config.ui.appearance, .dark)
        try expectEqual(config.ui.language, .chineseSimplified)
        try expectEqual(config.adjustments.watermarkText, "hello")
        try expectEqual(config.adjustments.datestampFormat, .olympus)
    }

    func testBridgeConfigEncodesPayload() throws {
        let original = try JSONDecoder().decode(
            BridgeConfig.self,
            from: Data(Self.fullConfigJSON.utf8)
        )
        let encoded = try JSONEncoder().encode(original)
        let roundTripped = try JSONDecoder().decode(BridgeConfig.self, from: encoded)
        try expectEqual(roundTripped, original)
    }

    func testFTPReceiveModeAllValuesDecode() throws {
        for mode in BridgeFTPReceiveMode.allCases {
            let data = Data("\"\(mode.rawValue)\"".utf8)
            let decoded = try JSONDecoder().decode(BridgeFTPReceiveMode.self, from: data)
            try expectEqual(decoded, mode)
        }
    }

    func testDatestampFormatAllValuesDecode() throws {
        for format in BridgeDatestampFormat.allCases {
            let data = Data("\"\(format.rawValue)\"".utf8)
            let decoded = try JSONDecoder().decode(BridgeDatestampFormat.self, from: data)
            try expectEqual(decoded, format)
        }
    }

    func testBridgeAdjustmentsConfigDecodesFullPayload() throws {
        let json = """
        {
          "preset": "Vivid",
          "saturation": 50,
          "exposure": -25,
          "sharpness": 10,
          "hue": -40,
          "vignette": 60,
          "datestamp": true,
          "datestamp_format": "modern",
          "watermark": true,
          "watermark_text": "InstantLink"
        }
        """
        let adjustments = try JSONDecoder().decode(
            BridgeAdjustmentsConfig.self,
            from: Data(json.utf8)
        )
        try expectEqual(adjustments.preset, "Vivid")
        try expectEqual(adjustments.saturation, 50)
        try expectEqual(adjustments.exposure, -25)
        try expectEqual(adjustments.sharpness, 10)
        try expectEqual(adjustments.hue, -40)
        try expectEqual(adjustments.vignette, 60)
        try expectTrue(adjustments.datestamp)
        try expectEqual(adjustments.datestampFormat, .modern)
        try expectTrue(adjustments.watermark)
        try expectEqual(adjustments.watermarkText, "InstantLink")
    }

    func testBridgeAdjustmentsConfigDefaultPresetIsDefault() throws {
        try expectEqual(BridgeAdjustmentsConfig.defaults.preset, "Default")
        try expectEqual(BridgeAdjustmentsConfig.defaults.saturation, 0)
        try expectEqual(BridgeAdjustmentsConfig.defaults.exposure, 0)
        try expectEqual(BridgeAdjustmentsConfig.defaults.sharpness, 0)
        try expectEqual(BridgeAdjustmentsConfig.defaults.hue, 0)
        try expectEqual(BridgeAdjustmentsConfig.defaults.vignette, 0)
        try expectFalse(BridgeAdjustmentsConfig.defaults.datestamp)
        try expectFalse(BridgeAdjustmentsConfig.defaults.watermark)
        try expectEqual(BridgeAdjustmentsConfig.defaults.watermarkText, "")
    }

    func testBridgeAdjustmentsConfigEncodesAllFields() throws {
        let original = BridgeAdjustmentsConfig(
            preset: "Custom3",
            saturation: -75,
            exposure: 80,
            sharpness: -100,
            hue: 100,
            vignette: 45,
            datestamp: true,
            datestampFormat: .labPrint,
            watermark: true,
            watermarkText: "Hello"
        )
        let encoded = try JSONEncoder().encode(original)
        let roundTripped = try JSONDecoder().decode(
            BridgeAdjustmentsConfig.self,
            from: encoded
        )
        try expectEqual(roundTripped, original)
    }

    func testUnknownEnumValueFailsGracefully() throws {
        let data = Data("\"neon\"".utf8)
        do {
            _ = try JSONDecoder().decode(BridgeUIAppearance.self, from: data)
            throw MacTestFailure(
                file: #filePath,
                line: #line,
                message: "Expected unknown enum value to throw"
            )
        } catch is DecodingError {
            // expected
        }
    }

    private static let fullConfigJSON = """
    {
      "ftp": {
        "mode": "peer",
        "username": "camera",
        "password_set": true
      },
      "printer": {
        "model": "mini_link3",
        "fit": "crop",
        "quality": 80,
        "keepalive_interval_s": 30,
        "search_interval_s": 30
      },
      "workflow": {
        "auto_print_delay_s": "off",
        "allow_print_without_film": true
      },
      "power": {
        "backend": "x306",
        "idle_poweroff_enabled": true,
        "idle_poweroff_after_s": 7200
      },
      "ui": {
        "appearance": "dark",
        "font_size": "large",
        "language": "zh-Hans"
      },
      "adjustments": {
        "preset": "Vivid",
        "saturation": 25,
        "exposure": -10,
        "sharpness": 5,
        "hue": 0,
        "vignette": 30,
        "datestamp": true,
        "datestamp_format": "olympus",
        "watermark": false,
        "watermark_text": "hello"
      }
    }
    """
}
