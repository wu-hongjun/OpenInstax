import AppKit
import Foundation

struct QueueImportItem {
    let url: URL
    let image: NSImage
    let imageDate: Date?
    let imageLocation: ImageLocationMetadata?
}

struct QueueEditingSnapshot: Equatable {
    var fitMode: String
    var cropOffset: CGSize
    var cropZoom: CGFloat
    var rotationAngle: Int
    var isHorizontallyFlipped: Bool
    var overlays: [OverlayItem]
    var filmOrientation: String

    init(editState: QueueItemEditState) {
        fitMode = editState.fitMode
        cropOffset = editState.cropOffset
        cropZoom = editState.cropZoom
        rotationAngle = editState.rotationAngle
        isHorizontallyFlipped = editState.isHorizontallyFlipped
        overlays = editState.overlays
        filmOrientation = editState.filmOrientation
    }

    var editState: QueueItemEditState {
        QueueItemEditState(
            fitMode: fitMode,
            cropOffset: cropOffset,
            cropZoom: cropZoom,
            rotationAngle: rotationAngle,
            isHorizontallyFlipped: isHorizontallyFlipped,
            overlays: overlays,
            filmOrientation: filmOrientation
        )
    }
}

struct QueueSelectionUpdate: Equatable {
    let selectedQueueIndex: Int
    let editing: QueueEditingSnapshot
}

struct QueueAddResult: Equatable {
    let selection: QueueSelectionUpdate
    let addedCount: Int
    let droppedCount: Int

    var hitQueueLimit: Bool {
        droppedCount > 0
    }
}

@MainActor
final class QueueEditCoordinator: ObservableObject {
    static let maxQueueItems = 20

    @Published private(set) var queue: [QueueItem]
    @Published private(set) var selectedQueueIndex: Int
    @Published var newPhotoDefaults: NewPhotoDefaults {
        didSet {
            newPhotoDefaults.save()
        }
    }

    private var savedFileModeEditState: QueueItemEditState?

    init(
        queue: [QueueItem] = [],
        selectedQueueIndex: Int = 0,
        newPhotoDefaults: NewPhotoDefaults = initialNewPhotoDefaults
    ) {
        self.queue = queue
        self.selectedQueueIndex = queue.indices.contains(selectedQueueIndex) ? selectedQueueIndex : 0
        self.newPhotoDefaults = newPhotoDefaults
    }

    var selectedItem: QueueItem? {
        guard queue.indices.contains(selectedQueueIndex) else { return nil }
        return queue[selectedQueueIndex]
    }

    var selectedImage: NSImage? { selectedItem?.image }
    var selectedImagePath: String? { selectedItem?.url.path }
    var selectedImageDate: Date? { selectedItem?.imageDate }
    var selectedImageLocation: ImageLocationMetadata? { selectedItem?.imageLocation }

    var printableQueueCountFromSelection: Int {
        guard !queue.isEmpty else { return 0 }
        let startIndex = queue.indices.contains(selectedQueueIndex) ? selectedQueueIndex : 0
        return queue.count - startIndex
    }

    var selectedOrDefaultEditingSnapshot: QueueEditingSnapshot {
        if let selectedItem {
            return QueueEditingSnapshot(editState: selectedItem.editState)
        }
        if let savedFileModeEditState {
            return QueueEditingSnapshot(editState: savedFileModeEditState)
        }
        return QueueEditingSnapshot(editState: makeQueueItemEditStateFromDefaults())
    }

    func persistSelectedQueueItemEditState(_ snapshot: QueueEditingSnapshot, isFileMode: Bool) {
        guard isFileMode, queue.indices.contains(selectedQueueIndex) else { return }
        queue[selectedQueueIndex].editState = snapshot.editState
    }

    @discardableResult
    func addItems(_ items: [QueueImportItem], currentEditing: QueueEditingSnapshot?) -> QueueAddResult {
        persistCurrentSelection(currentEditing)
        let previousSelection = selectedQueueIndex
        let hadSelection = queue.indices.contains(previousSelection)
        let initialCount = queue.count

        for item in items {
            if queue.count >= Self.maxQueueItems { break }
            queue.append(QueueItem(
                url: item.url,
                image: item.image,
                imageDate: item.imageDate,
                imageLocation: item.imageLocation,
                editState: makeNewQueueItemEditState()
            ))
        }

        if initialCount == 0, !queue.isEmpty {
            selectedQueueIndex = queue.count - 1
        } else if hadSelection, queue.indices.contains(previousSelection) {
            selectedQueueIndex = previousSelection
        } else if !queue.isEmpty {
            selectedQueueIndex = min(selectedQueueIndex, queue.count - 1)
        }

        let addedCount = queue.count - initialCount
        return QueueAddResult(
            selection: currentSelectionUpdate(),
            addedCount: addedCount,
            droppedCount: max(0, items.count - addedCount)
        )
    }

    @discardableResult
    func appendCapturedItem(
        _ item: QueueImportItem,
        filmOrientation: String,
        isHorizontallyFlipped: Bool,
        currentEditing: QueueEditingSnapshot?
    ) -> QueueSelectionUpdate? {
        persistCurrentSelection(currentEditing)
        guard queue.count < Self.maxQueueItems else { return nil }

        queue.append(QueueItem(
            url: item.url,
            image: item.image,
            imageDate: item.imageDate,
            imageLocation: item.imageLocation,
            editState: makeCapturedQueueItemEditState(
                filmOrientation: filmOrientation,
                isHorizontallyFlipped: isHorizontallyFlipped
            )
        ))

        selectedQueueIndex = queue.count - 1
        return currentSelectionUpdate()
    }

    @discardableResult
    func removeQueueItem(at index: Int, currentEditing: QueueEditingSnapshot?) -> QueueSelectionUpdate? {
        guard queue.indices.contains(index) else { return nil }
        persistCurrentSelection(currentEditing)

        let wasSelected = index == selectedQueueIndex
        queue.remove(at: index)

        if queue.isEmpty {
            selectedQueueIndex = 0
            return currentSelectionUpdate()
        }

        if wasSelected {
            selectedQueueIndex = min(index, queue.count - 1)
        } else if index < selectedQueueIndex {
            selectedQueueIndex -= 1
        }

        return currentSelectionUpdate()
    }

    @discardableResult
    func removeSelectedQueueItem(currentEditing: QueueEditingSnapshot?) -> QueueSelectionUpdate? {
        guard queue.indices.contains(selectedQueueIndex) else { return nil }
        return removeQueueItem(at: selectedQueueIndex, currentEditing: currentEditing)
    }

    @discardableResult
    func selectQueueItem(at index: Int, currentEditing: QueueEditingSnapshot?) -> QueueSelectionUpdate? {
        guard queue.indices.contains(index) else { return nil }
        persistCurrentSelection(currentEditing)
        selectedQueueIndex = index
        return currentSelectionUpdate()
    }

    @discardableResult
    func moveQueueItem(
        from source: Int,
        to destination: Int,
        currentEditing: QueueEditingSnapshot?
    ) -> QueueSelectionUpdate? {
        guard queue.indices.contains(source),
              destination >= 0,
              destination < queue.count else {
            return nil
        }

        persistCurrentSelection(currentEditing)
        let item = queue.remove(at: source)
        queue.insert(item, at: destination)
        selectedQueueIndex = destination
        return currentSelectionUpdate()
    }

    func beginCameraDraft(from fileModeEditing: QueueEditingSnapshot?) -> QueueEditingSnapshot {
        if let fileModeEditing {
            savedFileModeEditState = fileModeEditing.editState
        } else if let selectedItem {
            savedFileModeEditState = selectedItem.editState
        } else {
            savedFileModeEditState = nil
        }
        return QueueEditingSnapshot(editState: makeCameraDraftEditState())
    }

    func restoreFileModeAfterCamera() -> QueueEditingSnapshot {
        defer { savedFileModeEditState = nil }
        if let selectedItem {
            return QueueEditingSnapshot(editState: selectedItem.editState)
        }
        if let savedFileModeEditState {
            return QueueEditingSnapshot(editState: savedFileModeEditState)
        }
        return QueueEditingSnapshot(editState: makeQueueItemEditStateFromDefaults())
    }

    @discardableResult
    func saveCurrentSettingsAsNewPhotoDefaults(from snapshot: QueueEditingSnapshot) -> QueueEditingSnapshot? {
        newPhotoDefaults = NewPhotoDefaults(
            fitMode: snapshot.fitMode,
            overlays: defaultEligibleOverlays(from: snapshot.overlays),
            filmOrientation: snapshot.filmOrientation
        )
        return queue.isEmpty
            ? QueueEditingSnapshot(editState: makeQueueItemEditStateFromDefaults())
            : nil
    }

    @discardableResult
    func resetNewPhotoDefaults() -> QueueEditingSnapshot? {
        newPhotoDefaults = NewPhotoDefaults()
        return queue.isEmpty
            ? QueueEditingSnapshot(editState: makeQueueItemEditStateFromDefaults())
            : nil
    }

    // MARK: - Internal queue/edit helpers

    private func persistCurrentSelection(_ currentEditing: QueueEditingSnapshot?) {
        guard let currentEditing,
              queue.indices.contains(selectedQueueIndex) else { return }
        queue[selectedQueueIndex].editState = currentEditing.editState
    }

    private func currentSelectionUpdate() -> QueueSelectionUpdate {
        QueueSelectionUpdate(
            selectedQueueIndex: selectedQueueIndex,
            editing: selectedOrDefaultEditingSnapshot
        )
    }

    private func makeQueueItemEditStateFromDefaults() -> QueueItemEditState {
        QueueItemEditState(
            fitMode: newPhotoDefaults.fitMode,
            overlays: newPhotoDefaults.overlays,
            filmOrientation: newPhotoDefaults.filmOrientation
        )
    }

    private func makeNewQueueItemEditState() -> QueueItemEditState {
        makeQueueItemEditStateFromDefaults()
    }

    private func makeCameraDraftEditState() -> QueueItemEditState {
        makeQueueItemEditStateFromDefaults()
    }

    private func makeCapturedQueueItemEditState(
        filmOrientation: String,
        isHorizontallyFlipped: Bool
    ) -> QueueItemEditState {
        var editState = makeQueueItemEditStateFromDefaults()
        editState.filmOrientation = filmOrientation
        editState.isHorizontallyFlipped = isHorizontallyFlipped
        return editState
    }

    private func defaultEligibleOverlays(from overlays: [OverlayItem]) -> [OverlayItem] {
        overlays
            .filter { overlay in
                if case .timestamp = overlay.content {
                    return true
                }
                return false
            }
            .enumerated()
            .map { index, overlay in
                var overlay = overlay
                overlay.zIndex = index
                return overlay
            }
    }
}
