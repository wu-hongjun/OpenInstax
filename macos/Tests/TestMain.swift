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
            ("BridgeKeychainTests.testSaveAndLoadIdentityRoundTrip", {
                try BridgeKeychainTests().testSaveAndLoadIdentityRoundTrip()
            }),
            ("BridgeKeychainTests.testDeleteIdentityRemovesEntry", {
                try BridgeKeychainTests().testDeleteIdentityRemovesEntry()
            }),
            ("BridgeKeychainTests.testListIdentitiesReturnsAllSavedDevices", {
                try BridgeKeychainTests().testListIdentitiesReturnsAllSavedDevices()
            }),
            ("BridgeKeychainTests.testLoadMissingIdentityReturnsNil", {
                try BridgeKeychainTests().testLoadMissingIdentityReturnsNil()
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
