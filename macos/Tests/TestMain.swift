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
