import Foundation

@main
struct InstantLinkMacOSTestRunner {
    @MainActor
    static func main() async {
        let tests: [(String, @MainActor () async throws -> Void)] = [
            ("AppModelsTests.testPrinterProfileParsesSerialNumberFromInstaxIdentifier", {
                let suite = AppModelsTests()
                suite.setUp()
                defer { suite.tearDown() }
                try suite.testPrinterProfileParsesSerialNumberFromInstaxIdentifier()
            }),
            ("AppModelsTests.testPrinterModelCatalogReturnsExpectedAspectRatioAndTag", {
                let suite = AppModelsTests()
                suite.setUp()
                defer { suite.tearDown() }
                try suite.testPrinterModelCatalogReturnsExpectedAspectRatioAndTag()
            }),
            ("AppModelsTests.testNewPhotoDefaultsSanitizedKeepsOnlyFirstTimestampOverlay", {
                let suite = AppModelsTests()
                suite.setUp()
                defer { suite.tearDown() }
                try suite.testNewPhotoDefaultsSanitizedKeepsOnlyFirstTimestampOverlay()
            }),
            ("AppRuntimeServicesTests.testCompareVersionsHandlesPrefixesSuffixesAndDifferentLengths", {
                try AppRuntimeServicesTests().testCompareVersionsHandlesPrefixesSuffixesAndDifferentLengths()
            }),
            ("BridgeFirmwareBundleTests.testPackageLoadsBundledFirmwareMetadata", {
                try BridgeFirmwareBundleTests().testPackageLoadsBundledFirmwareMetadata()
            }),
            ("BridgeFirmwareBundleTests.testPackageReturnsNilWhenSidecarsAreMissing", {
                try BridgeFirmwareBundleTests().testPackageReturnsNilWhenSidecarsAreMissing()
            }),
            ("BridgeFirmwareBundleTests.testPackageMapsToBridgeUpdatePackage", {
                try BridgeFirmwareBundleTests().testPackageMapsToBridgeUpdatePackage()
            }),
            ("BridgeModelsTests.testDecodesStableStatusEnvelope", {
                try BridgeModelsTests().testDecodesStableStatusEnvelope()
            }),
            ("BridgeModelsTests.testDecodesAuthRequiredErrorEnvelope", {
                try BridgeModelsTests().testDecodesAuthRequiredErrorEnvelope()
            }),
            ("BridgeModelsTests.testDecodesPairingStatusEnvelope", {
                try BridgeModelsTests().testDecodesPairingStatusEnvelope()
            }),
            ("BridgeModelsTests.testDecodesStatusEnvelopeWithSystemStats", {
                try BridgeModelsTests().testDecodesStatusEnvelopeWithSystemStats()
            }),
            ("BridgeModelsTests.testDecodesStatusEnvelopeWithoutSystemStatsLeavesNil", {
                try BridgeModelsTests().testDecodesStatusEnvelopeWithoutSystemStatsLeavesNil()
            }),
            ("BridgeModelsTests.testSystemStatsFormattersHandleNilValues", {
                try BridgeModelsTests().testSystemStatsFormattersHandleNilValues()
            }),
            ("BridgeModelsTests.testSystemStatsFormattersWithKnownValues", {
                try BridgeModelsTests().testSystemStatsFormattersWithKnownValues()
            }),
            ("BridgeHTTPTransportTests.testCanonicalRequestPayloadMatchesBridgeManagerContract", {
                try BridgeHTTPTransportTests().testCanonicalRequestPayloadMatchesBridgeManagerContract()
            }),
            ("BridgeHTTPTransportTests.testHTTPTransportReadsHelloAndPairingStatusWithoutAuth", {
                try await BridgeHTTPTransportTests().testHTTPTransportReadsHelloAndPairingStatusWithoutAuth()
            }),
            ("BridgeHTTPTransportTests.testHTTPTransportSignsAdminStatusRequest", {
                try await BridgeHTTPTransportTests().testHTTPTransportSignsAdminStatusRequest()
            }),
            ("BridgeHTTPTransportTests.testCompletePairingSavesIdentityOnlyAfterServerAcceptsRequest", {
                try await BridgeHTTPTransportTests().testCompletePairingSavesIdentityOnlyAfterServerAcceptsRequest()
            }),
            ("BridgeHTTPTransportTests.testCompletePairingDoesNotSaveIdentityWhenServerRouteIsNotReady", {
                try await BridgeHTTPTransportTests().testCompletePairingDoesNotSaveIdentityWhenServerRouteIsNotReady()
            }),
            ("BridgeHTTPTransportTests.testUSBAutoTrustSendsExpectedPayloadAndDoesNotSignRequest", {
                try await BridgeHTTPTransportTests().testUSBAutoTrustSendsExpectedPayloadAndDoesNotSignRequest()
            }),
            ("BridgeHTTPTransportTests.testUSBAutoTrustSurfacesRejectionError", {
                try await BridgeHTTPTransportTests().testUSBAutoTrustSurfacesRejectionError()
            }),
            ("BridgeHTTPTransportTests.testForgetLocalAuthDeletesOnlyLocalIdentity", {
                try await BridgeHTTPTransportTests().testForgetLocalAuthDeletesOnlyLocalIdentity()
            }),
            ("BridgeHTTPTransportTests.testStartUpdateRequiresAllowedPreflightBeforeInstall", {
                try await BridgeHTTPTransportTests().testStartUpdateRequiresAllowedPreflightBeforeInstall()
            }),
            ("BridgeHTTPTransportTests.testUploadUpdateSignsAndPostsArchiveBytes", {
                try await BridgeHTTPTransportTests().testUploadUpdateSignsAndPostsArchiveBytes()
            }),
            ("BridgeHTTPTransportTests.testCreateBackupSignsRequestAndDecodesResult", {
                try await BridgeHTTPTransportTests().testCreateBackupSignsRequestAndDecodesResult()
            }),
            ("BridgeHTTPTransportTests.testRestoreBackupPostsBackupID", {
                try await BridgeHTTPTransportTests().testRestoreBackupPostsBackupID()
            }),
            ("BridgeHTTPTransportTests.testMarkUpdateGoodDecodesDoneState", {
                try await BridgeHTTPTransportTests().testMarkUpdateGoodDecodesDoneState()
            }),
            ("BridgeHTTPTransportTests.testRollbackUpdatePostsReasonAndDecodesState", {
                try await BridgeHTTPTransportTests().testRollbackUpdatePostsReasonAndDecodesState()
            }),
            ("BridgeTransportTests.testInMemoryTransportThrowsAuthRequiredForManagedEndpoints", {
                try await BridgeTransportTests().testInMemoryTransportThrowsAuthRequiredForManagedEndpoints()
            }),
            ("BridgeTransportTests.testInMemoryTransportUpdateFlowAdvancesToDone", {
                try await BridgeTransportTests().testInMemoryTransportUpdateFlowAdvancesToDone()
            }),
            ("BridgeTransportTests.testInMemoryTransportSupportsBackupUploadMarkGoodAndRollback", {
                try await BridgeTransportTests().testInMemoryTransportSupportsBackupUploadMarkGoodAndRollback()
            }),
            ("BridgeTransportTests.testInMemoryTransportManagedBackupRequiresAuth", {
                try await BridgeTransportTests().testInMemoryTransportManagedBackupRequiresAuth()
            }),
            ("QueueEditCoordinatorTests.testAddItemsEnforcesQueueLimitAndReportsDroppedItems", {
                let suite = QueueEditCoordinatorTests()
                suite.setUp()
                defer { suite.tearDown() }
                try suite.testAddItemsEnforcesQueueLimitAndReportsDroppedItems()
            }),
            ("QueueEditCoordinatorTests.testAddRemoveAndMovePreserveExpectedSelectionBehavior", {
                let suite = QueueEditCoordinatorTests()
                suite.setUp()
                defer { suite.tearDown() }
                try suite.testAddRemoveAndMovePreserveExpectedSelectionBehavior()
            }),
            ("QueueEditCoordinatorTests.testSaveTimestampOverlayAsDefaultsKeepsOnlyOneTimestampOverlay", {
                let suite = QueueEditCoordinatorTests()
                suite.setUp()
                defer { suite.tearDown() }
                try suite.testSaveTimestampOverlayAsDefaultsKeepsOnlyOneTimestampOverlay()
            }),
            ("QueueEditCoordinatorTests.testSaveCurrentLayoutAsDefaultsPersistsOnlyLayoutFields", {
                let suite = QueueEditCoordinatorTests()
                suite.setUp()
                defer { suite.tearDown() }
                try suite.testSaveCurrentLayoutAsDefaultsPersistsOnlyLayoutFields()
            }),
            ("QueueEditCoordinatorTests.testCameraDraftRestoreReturnsSavedEditStateWhenQueueIsEmpty", {
                let suite = QueueEditCoordinatorTests()
                suite.setUp()
                defer { suite.tearDown() }
                try suite.testCameraDraftRestoreReturnsSavedEditStateWhenQueueIsEmpty()
            }),
            ("PrinterConnectionCoordinatorTests.testTargetedReconnectSuccessUpdatesConnectedSnapshot", {
                try await PrinterConnectionCoordinatorTests().testTargetedReconnectSuccessUpdatesConnectedSnapshot()
            }),
            ("PrinterConnectionCoordinatorTests.testFailedReconnectFallsBackToRecoveryScan", {
                try await PrinterConnectionCoordinatorTests().testFailedReconnectFallsBackToRecoveryScan()
            }),
            ("PrinterConnectionCoordinatorTests.testRefreshDropsConnectedStateWhenStatusIsUnavailable", {
                try await PrinterConnectionCoordinatorTests().testRefreshDropsConnectedStateWhenStatusIsUnavailable()
            }),
            ("PrinterConnectionCoordinatorTests.testDeletingActiveProfileReentersPairingFlow", {
                try await PrinterConnectionCoordinatorTests().testDeletingActiveProfileReentersPairingFlow()
            }),
            ("PrinterConnectionCoordinatorTests.testStaleReconnectResultDoesNotOverrideNewPairingSession", {
                try await PrinterConnectionCoordinatorTests().testStaleReconnectResultDoesNotOverrideNewPairingSession()
            }),
            ("PrinterConnectionCoordinatorTests.testStopPairingLoopPreventsDelayedReconnectFromApplying", {
                try await PrinterConnectionCoordinatorTests().testStopPairingLoopPreventsDelayedReconnectFromApplying()
            }),
            ("BridgeClientFileStoreTests.testSaveAndLoadIdentityRoundTrip", {
                try BridgeClientFileStoreTests().testSaveAndLoadIdentityRoundTrip()
            }),
            ("BridgeClientFileStoreTests.testDeleteIdentityRemovesEntry", {
                try BridgeClientFileStoreTests().testDeleteIdentityRemovesEntry()
            }),
            ("BridgeClientFileStoreTests.testListIdentitiesReturnsAllSaved", {
                try BridgeClientFileStoreTests().testListIdentitiesReturnsAllSaved()
            }),
            ("BridgeClientFileStoreTests.testLoadMissingIdentityReturnsNil", {
                try BridgeClientFileStoreTests().testLoadMissingIdentityReturnsNil()
            }),
            ("BridgeClientFileStoreTests.testCreatesParentDirectoryWhenMissing", {
                try BridgeClientFileStoreTests().testCreatesParentDirectoryWhenMissing()
            }),
            ("BridgeClientFileStoreTests.testFileHas0600Permissions", {
                try BridgeClientFileStoreTests().testFileHas0600Permissions()
            }),
            ("BridgeClientFileStoreTests.testAtomicWriteIsTornWriteSafe", {
                try BridgeClientFileStoreTests().testAtomicWriteIsTornWriteSafe()
            }),
            ("BridgeClientFileStoreTests.testSigningKeyRoundTripsAsBase64URL", {
                try BridgeClientFileStoreTests().testSigningKeyRoundTripsAsBase64URL()
            }),
            ("BridgeClientFileStoreTests.testReadSelfHealsOnCorruptedJSON", {
                try BridgeClientFileStoreTests().testReadSelfHealsOnCorruptedJSON()
            }),
            ("BridgeClientFileStoreTests.testPersistsAcrossInstanceReloads", {
                try BridgeClientFileStoreTests().testPersistsAcrossInstanceReloads()
            }),
            ("BridgePairingViewModelTests.testCodeValidationRejectsNonDigits", {
                try BridgePairingViewModelTests().testCodeValidationRejectsNonDigits()
            }),
            ("BridgePairingViewModelTests.testCodeValidationRequiresSixDigits", {
                try BridgePairingViewModelTests().testCodeValidationRequiresSixDigits()
            }),
            ("BridgePairingViewModelTests.testSanitizeTruncatesToMaximum", {
                try BridgePairingViewModelTests().testSanitizeTruncatesToMaximum()
            }),
            ("BridgeControlCoordinatorTests.testDiscoveryFoundEmitsDeviceSnapshot", {
                try await BridgeControlCoordinatorTests().testDiscoveryFoundEmitsDeviceSnapshot()
            }),
            ("BridgeControlCoordinatorTests.testDiscoveryLossClearsSnapshot", {
                try await BridgeControlCoordinatorTests().testDiscoveryLossClearsSnapshot()
            }),
            ("BridgeControlCoordinatorTests.testPairingStateProgressesOnSuccessfulCompletion", {
                try await BridgeControlCoordinatorTests().testPairingStateProgressesOnSuccessfulCompletion()
            }),
            ("BridgeControlCoordinatorTests.testPairingFailureSurfaceErrorAndStaysUnpaired", {
                try await BridgeControlCoordinatorTests().testPairingFailureSurfaceErrorAndStaysUnpaired()
            }),
            ("BridgeControlCoordinatorTests.testStatusPollingPopulatesStatusWhenPaired", {
                try await BridgeControlCoordinatorTests().testStatusPollingPopulatesStatusWhenPaired()
            }),
            ("BridgeControlCoordinatorTests.testForgetClearsIdentityAndReturnsToUnpaired", {
                try await BridgeControlCoordinatorTests().testForgetClearsIdentityAndReturnsToUnpaired()
            }),
            ("BridgeControlCoordinatorTests.testCoordinatorPausesPollingWhenWindowHides", {
                try await BridgeControlCoordinatorTests().testCoordinatorPausesPollingWhenWindowHides()
            }),
            ("BridgeControlCoordinatorTests.testDiscoveryOverUSBTriggersAutoTrust", {
                try await BridgeControlCoordinatorTests().testDiscoveryOverUSBTriggersAutoTrust()
            }),
            ("BridgeControlCoordinatorTests.testDiscoveryOverWiFiDoesNotAutoTrust", {
                try await BridgeControlCoordinatorTests().testDiscoveryOverWiFiDoesNotAutoTrust()
            }),
            ("BridgeControlCoordinatorTests.testAutoTrustFailureLeavesPairingUnpaired", {
                try await BridgeControlCoordinatorTests().testAutoTrustFailureLeavesPairingUnpaired()
            }),
            ("BridgeControlCoordinatorTests.testAutoTrustSkippedWhenIdentityAlreadySaved", {
                try await BridgeControlCoordinatorTests().testAutoTrustSkippedWhenIdentityAlreadySaved()
            }),
            ("BridgeControlCoordinatorTests.testLastAutoTrustEventPublishedOnSuccess", {
                try await BridgeControlCoordinatorTests().testLastAutoTrustEventPublishedOnSuccess()
            }),
            ("BridgeConfigTests.testBridgeConfigDecodesFullPayload", {
                try BridgeConfigTests().testBridgeConfigDecodesFullPayload()
            }),
            ("BridgeConfigTests.testBridgeConfigEncodesPayload", {
                try BridgeConfigTests().testBridgeConfigEncodesPayload()
            }),
            ("BridgeConfigTests.testFTPReceiveModeAllValuesDecode", {
                try BridgeConfigTests().testFTPReceiveModeAllValuesDecode()
            }),
            ("BridgeConfigTests.testDatestampFormatAllValuesDecode", {
                try BridgeConfigTests().testDatestampFormatAllValuesDecode()
            }),
            ("BridgeConfigTests.testUnknownEnumValueFailsGracefully", {
                try BridgeConfigTests().testUnknownEnumValueFailsGracefully()
            }),
            ("BridgeConfigTests.testBridgeAdjustmentsConfigDecodesFullPayload", {
                try BridgeConfigTests().testBridgeAdjustmentsConfigDecodesFullPayload()
            }),
            ("BridgeConfigTests.testBridgeAdjustmentsConfigDefaultPresetIsDefault", {
                try BridgeConfigTests().testBridgeAdjustmentsConfigDefaultPresetIsDefault()
            }),
            ("BridgeConfigTests.testBridgeAdjustmentsConfigEncodesAllFields", {
                try BridgeConfigTests().testBridgeAdjustmentsConfigEncodesAllFields()
            }),
            ("BridgeSettingsDraftTests.testLoadSetsDraftEqualToCanonical", {
                try BridgeSettingsDraftTests().testLoadSetsDraftEqualToCanonical()
            }),
            ("BridgeSettingsDraftTests.testRevertResetsToLoaded", {
                try BridgeSettingsDraftTests().testRevertResetsToLoaded()
            }),
            ("BridgeSettingsDraftTests.testIsDirtyDetectsSingleFieldChange", {
                try BridgeSettingsDraftTests().testIsDirtyDetectsSingleFieldChange()
            }),
            ("BridgeSettingsDraftTests.testDiffIncludesOnlyChangedFields", {
                try BridgeSettingsDraftTests().testDiffIncludesOnlyChangedFields()
            }),
            ("BridgeSettingsDraftTests.testDiffOmitsSectionWhenNoFieldsChanged", {
                try BridgeSettingsDraftTests().testDiffOmitsSectionWhenNoFieldsChanged()
            }),
            ("BridgeSettingsDraftTests.testValidateRejectsNegativeKeepalive", {
                try BridgeSettingsDraftTests().testValidateRejectsNegativeKeepalive()
            }),
            ("BridgeSettingsDraftTests.testValidateRejectsJPEGQualityOutOfRange", {
                try BridgeSettingsDraftTests().testValidateRejectsJPEGQualityOutOfRange()
            }),
            ("BridgeSettingsDraftTests.testValidatePassesOnDefaults", {
                try BridgeSettingsDraftTests().testValidatePassesOnDefaults()
            }),
            ("BridgeSettingsDraftTests.testApplyStateTransitionsThroughSuccess", {
                try BridgeSettingsDraftTests().testApplyStateTransitionsThroughSuccess()
            }),
            ("BridgeSettingsDraftTests.testApplyStateTransitionsThroughFailure", {
                try BridgeSettingsDraftTests().testApplyStateTransitionsThroughFailure()
            }),
            ("BridgeSettingsDraftTests.testAdjustmentsValidationRejectsSaturationOutOfRange", {
                try BridgeSettingsDraftTests().testAdjustmentsValidationRejectsSaturationOutOfRange()
            }),
            ("BridgeSettingsDraftTests.testAdjustmentsValidationRejectsVignetteOutOfRange", {
                try BridgeSettingsDraftTests().testAdjustmentsValidationRejectsVignetteOutOfRange()
            }),
            ("BridgeSettingsDraftTests.testAdjustmentsValidationRejectsUnknownPreset", {
                try BridgeSettingsDraftTests().testAdjustmentsValidationRejectsUnknownPreset()
            }),
            ("BridgeSettingsDraftTests.testAdjustmentsDirtyTrackingDetectsSliderChange", {
                try BridgeSettingsDraftTests().testAdjustmentsDirtyTrackingDetectsSliderChange()
            }),
            ("BridgeSettingsDraftTests.testAdjustmentsDiffIncludesChangedFieldsOnly", {
                try BridgeSettingsDraftTests().testAdjustmentsDiffIncludesChangedFieldsOnly()
            }),
            ("BridgeSettingsDraftTests.testAdjustmentsRevertResetsSliders", {
                try BridgeSettingsDraftTests().testAdjustmentsRevertResetsSliders()
            }),
            ("BridgeSettingsApplyFlowTests.testApplyHappyPath", {
                try await BridgeSettingsApplyFlowTests().testApplyHappyPath()
            }),
            ("BridgeSettingsApplyFlowTests.testApplyValidationErrorSurfacesFieldErrors", {
                try await BridgeSettingsApplyFlowTests().testApplyValidationErrorSurfacesFieldErrors()
            }),
            ("BridgeSettingsApplyFlowTests.testApplyNetworkErrorShowsManagementUnavailable", {
                try await BridgeSettingsApplyFlowTests().testApplyNetworkErrorShowsManagementUnavailable()
            }),
            ("BridgeSettingsApplyFlowTests.testApplySkippedWhenClientValidationFails", {
                try await BridgeSettingsApplyFlowTests().testApplySkippedWhenClientValidationFails()
            }),
            ("BridgeSettingsApplyFlowTests.testApplyDoesNotMutateLoadedOnFailure", {
                try await BridgeSettingsApplyFlowTests().testApplyDoesNotMutateLoadedOnFailure()
            }),
            ("BridgeSettingsApplyFlowTests.testApplyAcceptsServerCounterproposal", {
                try await BridgeSettingsApplyFlowTests().testApplyAcceptsServerCounterproposal()
            }),
            ("BridgeHTTPTransportTests.testGetConfigSignsAndDecodes", {
                try await BridgeHTTPTransportTests().testGetConfigSignsAndDecodes()
            }),
            ("BridgeHTTPTransportTests.testPutConfigSignsAndSendsDiff", {
                try await BridgeHTTPTransportTests().testPutConfigSignsAndSendsDiff()
            }),
            ("BridgeHTTPTransportTests.testPutConfigSurfacesValidationError", {
                try await BridgeHTTPTransportTests().testPutConfigSurfacesValidationError()
            }),
            ("BridgeUpdateCoordinatorTests.testLoadBundleEmitsUpToDateWhenVersionsMatch", {
                try await BridgeUpdateCoordinatorTests().testLoadBundleEmitsUpToDateWhenVersionsMatch()
            }),
            ("BridgeUpdateCoordinatorTests.testLoadBundleEmitsUpdateAvailableWhenBundleIsNewer", {
                try await BridgeUpdateCoordinatorTests().testLoadBundleEmitsUpdateAvailableWhenBundleIsNewer()
            }),
            ("BridgeUpdateCoordinatorTests.testLoadBundleEmitsNoBundleWhenFirmwareMissing", {
                try await BridgeUpdateCoordinatorTests().testLoadBundleEmitsNoBundleWhenFirmwareMissing()
            }),
            ("BridgeUpdateCoordinatorTests.testRefreshPreflightPopulatesSnapshot", {
                try await BridgeUpdateCoordinatorTests().testRefreshPreflightPopulatesSnapshot()
            }),
            ("BridgeUpdateCoordinatorTests.testRunUpdateProgressesThroughAllPhases", {
                try await BridgeUpdateCoordinatorTests().testRunUpdateProgressesThroughAllPhases()
            }),
            ("BridgeUpdateCoordinatorTests.testRunUpdateSurfaceUploadErrorAsFailure", {
                try await BridgeUpdateCoordinatorTests().testRunUpdateSurfaceUploadErrorAsFailure()
            }),
            ("BridgeUpdateCoordinatorTests.testRunUpdateSurfaceStartErrorAsFailure", {
                try await BridgeUpdateCoordinatorTests().testRunUpdateSurfaceStartErrorAsFailure()
            }),
            ("BridgeUpdateCoordinatorTests.testReconnectTimeoutMarksFailure", {
                try await BridgeUpdateCoordinatorTests().testReconnectTimeoutMarksFailure()
            }),
            ("BridgeUpdateCoordinatorTests.testReconnectSuccessMarksGood", {
                try await BridgeUpdateCoordinatorTests().testReconnectSuccessMarksGood()
            }),
            ("BridgeUpdateCoordinatorTests.testRollbackEmitsRolledBackResult", {
                try await BridgeUpdateCoordinatorTests().testRollbackEmitsRolledBackResult()
            }),
            ("BridgeUpdateCoordinatorTests.testCompareVersionsRanksNumericComponents", {
                try await BridgeUpdateCoordinatorTests().testCompareVersionsRanksNumericComponents()
            }),
            ("BridgeBackupCoordinatorTests.testCreateBackupHappyPathWritesFile", {
                try await BridgeBackupCoordinatorTests().testCreateBackupHappyPathWritesFile()
            }),
            ("BridgeBackupCoordinatorTests.testCreateBackupFailureSurfacesError", {
                try await BridgeBackupCoordinatorTests().testCreateBackupFailureSurfacesError()
            }),
            ("BridgeBackupCoordinatorTests.testCreateBackupOmitsResultOnCancellation", {
                try await BridgeBackupCoordinatorTests().testCreateBackupOmitsResultOnCancellation()
            }),
            ("BridgeBackupCoordinatorTests.testRestoreBackupHappyPathTriggersReconnect", {
                try await BridgeBackupCoordinatorTests().testRestoreBackupHappyPathTriggersReconnect()
            }),
            ("BridgeBackupCoordinatorTests.testRestoreBackupValidationErrorSurfacesField", {
                try await BridgeBackupCoordinatorTests().testRestoreBackupValidationErrorSurfacesField()
            }),
            ("BridgeBackupCoordinatorTests.testRestoreBackupFromDifferentBridgeIDRequiresExtraConfirmation", {
                try await BridgeBackupCoordinatorTests().testRestoreBackupFromDifferentBridgeIDRequiresExtraConfirmation()
            }),
            ("BridgeBackupCoordinatorTests.testClearLastResultClearsResult", {
                try await BridgeBackupCoordinatorTests().testClearLastResultClearsResult()
            }),
            ("BridgeBackupCoordinatorTests.testBackupOperationProgressEmitted", {
                try await BridgeBackupCoordinatorTests().testBackupOperationProgressEmitted()
            }),
            ("BridgeDiagnosticsCoordinatorTests.testStartStreamingAppendsScriptedEventsAndCapsAtMaxTail", {
                try await BridgeDiagnosticsCoordinatorTests().testStartStreamingAppendsScriptedEventsAndCapsAtMaxTail()
            }),
            ("BridgeDiagnosticsCoordinatorTests.testStartStreamingTransitionsLiveToDisconnectedWhenSourceFinishes", {
                try await BridgeDiagnosticsCoordinatorTests().testStartStreamingTransitionsLiveToDisconnectedWhenSourceFinishes()
            }),
            ("BridgeDiagnosticsCoordinatorTests.testStopStreamingMarksSnapshotPausedAndPreservesTail", {
                try await BridgeDiagnosticsCoordinatorTests().testStopStreamingMarksSnapshotPausedAndPreservesTail()
            }),
            ("BridgeDiagnosticsCoordinatorTests.testSetFilterUpdatesSnapshotAndRestartsStreamWhenLive", {
                try await BridgeDiagnosticsCoordinatorTests().testSetFilterUpdatesSnapshotAndRestartsStreamWhenLive()
            }),
            ("BridgeDiagnosticsCoordinatorTests.testStreamingErrorSurfacesDisconnectedWithReason", {
                try await BridgeDiagnosticsCoordinatorTests().testStreamingErrorSurfacesDisconnectedWithReason()
            }),
            ("BridgeDiagnosticsCoordinatorTests.testCreateSupportBundleHappyPathReturnsResult", {
                try await BridgeDiagnosticsCoordinatorTests().testCreateSupportBundleHappyPathReturnsResult()
            }),
            ("BridgeDiagnosticsCoordinatorTests.testCreateSupportBundleWritesSidecarToDestination", {
                try await BridgeDiagnosticsCoordinatorTests().testCreateSupportBundleWritesSidecarToDestination()
            }),
            ("BridgeDiagnosticsCoordinatorTests.testCreateSupportBundleSurfacesError", {
                try await BridgeDiagnosticsCoordinatorTests().testCreateSupportBundleSurfacesError()
            }),
            ("BridgeDiagnosticsCoordinatorTests.testEvaluateHealthOnHelloFailureCrossesThresholdAndSurfacesBanner", {
                try await BridgeDiagnosticsCoordinatorTests().testEvaluateHealthOnHelloFailureCrossesThresholdAndSurfacesBanner()
            }),
            ("BridgeDiagnosticsCoordinatorTests.testEvaluateHealthOnHelloFailureWithoutUSBCarrierDoesNotSurfaceBanner", {
                try await BridgeDiagnosticsCoordinatorTests().testEvaluateHealthOnHelloFailureWithoutUSBCarrierDoesNotSurfaceBanner()
            }),
            ("BridgeDiagnosticsCoordinatorTests.testEvaluateHealthOnHelloSuccessTransitionsToRecovered", {
                try await BridgeDiagnosticsCoordinatorTests().testEvaluateHealthOnHelloSuccessTransitionsToRecovered()
            }),
            ("BridgeDiagnosticsCoordinatorTests.testAttemptRecoveryHappyPathStaysInRestartUntilHelloSucceeds", {
                try await BridgeDiagnosticsCoordinatorTests().testAttemptRecoveryHappyPathStaysInRestartUntilHelloSucceeds()
            }),
            ("BridgeDiagnosticsCoordinatorTests.testAttemptRecoveryRestartFailureSurfacesUnrecoverable", {
                try await BridgeDiagnosticsCoordinatorTests().testAttemptRecoveryRestartFailureSurfacesUnrecoverable()
            }),
            ("BridgeDiagnosticsCoordinatorTests.testDismissRecoveryFromRecoveredReturnsToOK", {
                try await BridgeDiagnosticsCoordinatorTests().testDismissRecoveryFromRecoveredReturnsToOK()
            }),
        ]

        var failures = 0
        for (name, body) in tests {
            do {
                print("Running \(name)")
                try await body()
                print("PASS \(name)")
            } catch {
                failures += 1
                writeError("FAIL \(name)")
                writeError("  \(error)")
            }
        }

        if failures == 0 {
            print("Executed \(tests.count) macOS tests, 0 failed.")
            exit(0)
        } else {
            writeError("Executed \(tests.count) macOS tests, \(failures) failed.")
            exit(1)
        }
    }

    private static func writeError(_ message: String) {
        FileHandle.standardError.write(Data((message + "\n").utf8))
    }
}
