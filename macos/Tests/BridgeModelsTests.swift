import Foundation

final class BridgeModelsTests {
    func testDecodesStableStatusEnvelope() throws {
        let data = Data(
            """
            {
              "schema_version": 1,
              "request_id": "req-status-1",
              "ok": true,
              "status": {
                "device_id": "IB-12345678",
                "display_name": "InstantLink Bridge IB-12345678",
                "bridge_version": "0.1.0",
                "api_version": "1",
                "readiness": "ready",
                "active_upload_mode": "bridge_wifi",
                "uptime_seconds": 42,
                "network": {
                  "mode": "bridge_wifi",
                  "label": "Bridge Wi-Fi",
                  "address": "192.168.8.1",
                  "connected": true
                },
                "printer": {
                  "display_name": "INSTAX-52006924",
                  "model": "Instax Mini Link 3",
                  "film_remaining": 7,
                  "battery_percent": 84,
                  "connected": true,
                  "busy": false
                },
                "update": {
                  "current_version": "0.1.0",
                  "available_version": "0.2.0",
                  "can_update": false,
                  "operation_id": null,
                  "phase": "idle"
                },
                "last_upload": {
                  "filename": "latest.jpg",
                  "received_at": "2026-05-26T12:00:00Z",
                  "printed_at": "2026-05-26T12:00:30Z",
                  "status": "printed"
                }
              }
            }
            """.utf8
        )

        let envelope = try JSONDecoder().decode(BridgeAPIEnvelope.self, from: data)
        let status = try envelope.requireStatus()

        try expectEqual(envelope.schemaVersion, 1)
        try expectEqual(envelope.requestID, "req-status-1")
        try expectEqual(status.deviceID, "IB-12345678")
        try expectEqual(status.readiness, .ready)
        try expectEqual(status.activeUploadMode, .bridgeWiFi)
        try expectEqual(status.network?.label, "Bridge Wi-Fi")
        try expectEqual(status.printer?.filmRemaining, 7)
        try expectEqual(status.update?.availableVersion, "0.2.0")
        try expectEqual(status.lastUpload?.status, "printed")
    }

    func testDecodesAuthRequiredErrorEnvelope() throws {
        let data = Data(
            """
            {
              "schema_version": 1,
              "request_id": "req-auth-1",
              "ok": false,
              "error_code": "auth_required",
              "error": {
                "message": "Bridge access requires pairing",
                "details": {
                  "pairing_window_open": false,
                  "next_step": "open_bridge_access"
                },
                "retry_after_seconds": 90
              }
            }
            """.utf8
        )

        let envelope = try JSONDecoder().decode(BridgeAPIEnvelope.self, from: data)

        do {
            try envelope.requireOK()
            throw MacTestFailure(
                file: #filePath,
                line: #line,
                message: "Expected auth-required envelope to throw"
            )
        } catch let error as BridgeAPIError {
            try expectEqual(error.requestID, "req-auth-1")
            try expectEqual(error.code, .authRequired)
            try expectEqual(error.payload.message, "Bridge access requires pairing")
            try expectEqual(error.payload.details["pairing_window_open"], .bool(false))
            try expectEqual(error.payload.details["next_step"], .string("open_bridge_access"))
            try expectEqual(error.payload.retryAfterSeconds, 90)
        }
    }

    func testDecodesStatusEnvelopeWithSystemStats() throws {
        let data = Data(
            """
            {
              "schema_version": 1,
              "request_id": "req-status-sys",
              "ok": true,
              "status": {
                "device_id": "IB-12345678",
                "display_name": "InstantLink Bridge IB-12345678",
                "bridge_version": "0.1.26",
                "api_version": "1",
                "readiness": "ready",
                "active_upload_mode": "bridge_wifi",
                "system_stats": {
                  "cpu_percent": 23.0,
                  "ram_used_mb": 297,
                  "ram_total_mb": 463,
                  "storage_used_gb": 6.3,
                  "storage_total_gb": 57.0,
                  "soc_temperature_c": 53.2
                }
              }
            }
            """.utf8
        )

        let envelope = try JSONDecoder().decode(BridgeAPIEnvelope.self, from: data)
        let status = try envelope.requireStatus()
        guard let stats = status.systemStats else {
            throw MacTestFailure(
                file: #filePath,
                line: #line,
                message: "expected system_stats to decode"
            )
        }

        try expectEqual(stats.cpuPercent, 23.0)
        try expectEqual(stats.ramUsedMB, 297)
        try expectEqual(stats.ramTotalMB, 463)
        try expectEqual(stats.storageUsedGB, 6.3)
        try expectEqual(stats.storageTotalGB, 57.0)
        try expectEqual(stats.socTemperatureC, 53.2)
    }

    func testDecodesStatusEnvelopeWithoutSystemStatsLeavesNil() throws {
        // Older bridges that pre-date the system_stats block omit the key
        // entirely. The Mac client must still decode the envelope; the
        // overview tab renders a "not available" fallback row in that case.
        let data = Data(
            """
            {
              "schema_version": 1,
              "request_id": "req-status-no-sys",
              "ok": true,
              "status": {
                "device_id": "IB-12345678",
                "display_name": "InstantLink Bridge IB-12345678",
                "bridge_version": "0.1.25",
                "api_version": "1",
                "readiness": "ready",
                "active_upload_mode": "bridge_wifi"
              }
            }
            """.utf8
        )

        let envelope = try JSONDecoder().decode(BridgeAPIEnvelope.self, from: data)
        let status = try envelope.requireStatus()
        try expectTrue(status.systemStats == nil)
    }

    func testSystemStatsFormattersHandleNilValues() throws {
        // When any reader on the bridge fails (no /proc/stat baseline yet,
        // missing thermal zone, statvfs error) the corresponding field is
        // null. All four formatters must return the em-dash sentinel so the
        // overview rows look intentional rather than `"0"` or `"-1"`.
        let empty = BridgeSystemStats(
            cpuPercent: nil,
            ramUsedMB: nil,
            ramTotalMB: nil,
            storageUsedGB: nil,
            storageTotalGB: nil,
            socTemperatureC: nil
        )

        try expectEqual(empty.formattedCPU, "—")
        try expectEqual(empty.formattedMemory, "—")
        try expectEqual(empty.formattedStorage, "—")
        try expectEqual(empty.formattedTemperature, "—")

        // Memory and storage need both sides; if only one is nil the formatter
        // still degrades to the em-dash so we never show "297 / — MB".
        let halfMemory = BridgeSystemStats(
            cpuPercent: nil,
            ramUsedMB: 297,
            ramTotalMB: nil,
            storageUsedGB: 6.3,
            storageTotalGB: nil,
            socTemperatureC: nil
        )
        try expectEqual(halfMemory.formattedMemory, "—")
        try expectEqual(halfMemory.formattedStorage, "—")
    }

    func testSystemStatsFormattersWithKnownValues() throws {
        // Mirrors the LCD About page's format_* helpers in
        // bridge/src/instantlink_bridge/system_stats.py so the Mac
        // Overview and on-device LCD always read the same.
        let stats = BridgeSystemStats(
            cpuPercent: 23.0,
            ramUsedMB: 297,
            ramTotalMB: 463,
            storageUsedGB: 6.3,
            storageTotalGB: 57.0,
            socTemperatureC: 53.2
        )

        try expectEqual(stats.formattedCPU, "23%")
        try expectEqual(stats.formattedMemory, "297 / 463 MB")
        try expectEqual(stats.formattedStorage, "6.3 / 57 GB")
        try expectEqual(stats.formattedTemperature, "53°C")

        // Edge: when used storage is over 10 GB the LCD drops the decimal so
        // the row reads "12 / 57 GB" instead of "12.0 / 57 GB".
        let bigStorage = BridgeSystemStats(
            cpuPercent: 7.4,
            ramUsedMB: 100,
            ramTotalMB: 463,
            storageUsedGB: 12.0,
            storageTotalGB: 57.0,
            socTemperatureC: 49.8
        )
        try expectEqual(bigStorage.formattedCPU, "7%")
        try expectEqual(bigStorage.formattedStorage, "12 / 57 GB")
        try expectEqual(bigStorage.formattedTemperature, "50°C")
    }

    func testDecodesPairingStatusEnvelope() throws {
        let data = Data(
            """
            {
              "schema_version": 1,
              "request_id": "req-pairing-1",
              "ok": true,
              "pairing": {
                "open": true,
                "auth_implemented": true,
                "confirmation_code_required": true,
                "expires_at": 1780500690,
                "expires_in_seconds": 90,
                "paired_client_id": "macbook",
                "authorized_client_count": 1
              }
            }
            """.utf8
        )

        let envelope = try JSONDecoder().decode(BridgeAPIEnvelope.self, from: data)
        let pairing = try envelope.requirePairingStatus()

        try expectTrue(pairing.open)
        try expectTrue(pairing.authImplemented)
        try expectTrue(pairing.confirmationCodeRequired)
        try expectEqual(pairing.expiresAt, 1780500690)
        try expectEqual(pairing.expiresInSeconds, 90)
        try expectEqual(pairing.pairedClientID, "macbook")
        try expectEqual(pairing.authorizedClientCount, 1)
    }
}
