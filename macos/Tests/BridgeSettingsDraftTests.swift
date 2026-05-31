import Foundation

@MainActor
final class BridgeSettingsDraftTests {
    func testLoadSetsDraftEqualToCanonical() throws {
        let draft = BridgeSettingsDraft()
        draft.load(.defaults)
        try expectEqual(draft.loaded, draft.draft)
        try expectFalse(draft.isDirty)
        try expectEqual(draft.applyState, .idle)
        try expectTrue(draft.fieldErrors.isEmpty)
    }

    func testRevertResetsToLoaded() throws {
        let draft = BridgeSettingsDraft()
        draft.load(.defaults)
        draft.draft?.printer.quality = 50
        try expectTrue(draft.isDirty)
        draft.revert()
        try expectFalse(draft.isDirty)
        try expectEqual(draft.draft, draft.loaded)
    }

    func testIsDirtyDetectsSingleFieldChange() throws {
        let draft = BridgeSettingsDraft()
        draft.load(.defaults)
        try expectFalse(draft.isDirty)
        draft.draft?.workflow.allowPrintWithoutFilm = true
        try expectTrue(draft.isDirty)
    }

    func testDiffIncludesOnlyChangedFields() throws {
        let draft = BridgeSettingsDraft()
        draft.load(.defaults)
        draft.draft?.printer.quality = 80
        draft.draft?.printer.fit = "crop"

        let diff = draft.diff()
        try expectTrue(diff.keys.contains("printer"))
        let printer = try unwrap(diff["printer"] as? [String: Any])
        try expectEqual(printer["quality"] as? Int, 80)
        try expectEqual(printer["fit"] as? String, "crop")
        // Untouched fields are NOT included.
        try expectFalse(printer.keys.contains("keepalive_interval_s"))
        try expectFalse(printer.keys.contains("search_interval_s"))
        try expectFalse(diff.keys.contains("ui"))
    }

    func testDiffOmitsSectionWhenNoFieldsChanged() throws {
        let draft = BridgeSettingsDraft()
        draft.load(.defaults)
        draft.draft?.adjustments.watermarkText = "ok"
        let diff = draft.diff()
        try expectFalse(diff.keys.contains("ftp"))
        try expectFalse(diff.keys.contains("printer"))
        try expectFalse(diff.keys.contains("workflow"))
        try expectFalse(diff.keys.contains("power"))
        try expectFalse(diff.keys.contains("ui"))
        try expectTrue(diff.keys.contains("adjustments"))
    }

    func testValidateRejectsNegativeKeepalive() throws {
        let draft = BridgeSettingsDraft()
        draft.load(.defaults)
        draft.draft?.printer.keepaliveIntervalSeconds = -1
        try expectFalse(draft.validate())
        try expectTrue(draft.fieldErrors[.printerKeepaliveInterval] != nil)
    }

    func testValidateRejectsJPEGQualityOutOfRange() throws {
        let draft = BridgeSettingsDraft()
        draft.load(.defaults)
        draft.draft?.printer.quality = 0
        try expectFalse(draft.validate())
        try expectTrue(draft.fieldErrors[.printerJPEGQuality] != nil)

        draft.draft?.printer.quality = 101
        try expectFalse(draft.validate())
        try expectTrue(draft.fieldErrors[.printerJPEGQuality] != nil)
    }

    func testValidatePassesOnDefaults() throws {
        let draft = BridgeSettingsDraft()
        draft.load(.defaults)
        try expectTrue(draft.validate())
        try expectTrue(draft.fieldErrors.isEmpty)
    }

    func testApplyStateTransitionsThroughSuccess() throws {
        let draft = BridgeSettingsDraft()
        draft.load(.defaults)
        try expectEqual(draft.applyState, .idle)
        draft.beginApplying()
        try expectEqual(draft.applyState, .applying)
        let after = Date()
        draft.recordApplySuccess(.defaults, at: after)
        try expectEqual(draft.applyState, .succeeded(at: after))
    }

    func testApplyStateTransitionsThroughFailure() throws {
        let draft = BridgeSettingsDraft()
        draft.load(.defaults)
        draft.beginApplying()
        draft.recordApplyFailure(
            message: "bad input",
            fieldErrors: ["printer.quality": "JPEG quality must be 1..100."]
        )
        try expectEqual(draft.applyState, .failed(message: "bad input"))
        try expectEqual(draft.fieldErrors[.printerJPEGQuality], "JPEG quality must be 1..100.")
    }

    func testAdjustmentsValidationRejectsSaturationOutOfRange() throws {
        let draft = BridgeSettingsDraft()
        draft.load(.defaults)
        draft.draft?.adjustments.saturation = 200
        try expectFalse(draft.validate())
        try expectTrue(draft.fieldErrors[.adjustmentsSaturation] != nil)
    }

    func testAdjustmentsValidationRejectsVignetteOutOfRange() throws {
        let draft = BridgeSettingsDraft()
        draft.load(.defaults)
        draft.draft?.adjustments.vignette = -10
        try expectFalse(draft.validate())
        try expectTrue(draft.fieldErrors[.adjustmentsVignette] != nil)
    }

    func testAdjustmentsValidationRejectsUnknownPreset() throws {
        let draft = BridgeSettingsDraft()
        draft.load(.defaults)
        draft.draft?.adjustments.preset = "Bogus"
        try expectFalse(draft.validate())
        try expectTrue(draft.fieldErrors[.adjustmentsPreset] != nil)
    }

    func testAdjustmentsDirtyTrackingDetectsSliderChange() throws {
        let draft = BridgeSettingsDraft()
        draft.load(.defaults)
        try expectFalse(draft.isDirty)
        draft.draft?.adjustments.saturation = 50
        try expectTrue(draft.isDirty)
    }

    func testAdjustmentsDiffIncludesChangedFieldsOnly() throws {
        let draft = BridgeSettingsDraft()
        draft.load(.defaults)
        draft.draft?.adjustments.exposure = 25
        let diff = draft.diff()
        let adjustments = try unwrap(diff["adjustments"] as? [String: Any])
        try expectEqual(adjustments["exposure"] as? Int, 25)
        try expectFalse(adjustments.keys.contains("saturation"))
        try expectFalse(adjustments.keys.contains("sharpness"))
        try expectFalse(adjustments.keys.contains("hue"))
        try expectFalse(adjustments.keys.contains("vignette"))
        try expectFalse(adjustments.keys.contains("preset"))
        try expectFalse(adjustments.keys.contains("datestamp"))
        try expectFalse(adjustments.keys.contains("watermark"))
        try expectFalse(adjustments.keys.contains("watermark_text"))
        try expectFalse(adjustments.keys.contains("datestamp_format"))
    }

    func testAdjustmentsRevertResetsSliders() throws {
        let draft = BridgeSettingsDraft()
        draft.load(.defaults)
        draft.draft?.adjustments.saturation = 40
        draft.draft?.adjustments.exposure = -20
        draft.draft?.adjustments.sharpness = 10
        draft.draft?.adjustments.hue = -50
        draft.draft?.adjustments.vignette = 70
        try expectTrue(draft.isDirty)
        draft.revert()
        try expectFalse(draft.isDirty)
        try expectEqual(draft.draft?.adjustments.saturation, 0)
        try expectEqual(draft.draft?.adjustments.exposure, 0)
        try expectEqual(draft.draft?.adjustments.sharpness, 0)
        try expectEqual(draft.draft?.adjustments.hue, 0)
        try expectEqual(draft.draft?.adjustments.vignette, 0)
    }

    private func unwrap<T>(_ value: T?) throws -> T {
        guard let value else {
            throw MacTestFailure(file: #filePath, line: #line, message: "Unexpected nil")
        }
        return value
    }
}
