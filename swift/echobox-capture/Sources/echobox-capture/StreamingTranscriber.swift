import Foundation
import WhisperKit

/// Streaming transcriber that accumulates Float32 mono samples at 16 kHz and
/// periodically runs WhisperKit on a sliding window. Emits partial and final
/// JSONL events as text becomes available.
///
/// Strategy: keep a ring buffer of the last `windowSeconds` of audio. On a
/// timer, transcribe the window. Compare the new transcript against the
/// previous one; the stable prefix is emitted as `final` (one event per new
/// segment) and the trailing tail is emitted as a single `partial`.
///
/// This is intentionally simpler than WhisperKit's `AudioStreamTranscriber`
/// because echobox already keeps the full WAV on disk and runs a higher-quality
/// post-call pass — the live stream only needs to be "good enough to read".
final class StreamingTranscriber {
    struct Config {
        var modelName: String
        var windowSeconds: Double
        var stepSeconds: Double
        var minNewAudioSeconds: Double
        var sampleRate: Double
    }

    private let config: Config
    private var whisperKit: WhisperKit?
    private var ringLock = NSLock()
    private var ring: [Float] = []
    private var totalSamples: Int = 0
    private var lastTranscribedSampleCount: Int = 0
    private var emittedFinalText: String = ""
    private var stopFlag = false
    private var workerTask: Task<Void, Never>?
    private var finalFlushDone: DispatchSemaphore?
    private let onEvent: ([String: Any]) -> Void
    private(set) var ready: Bool = false
    private(set) var initError: String?

    init(config: Config, onEvent: @escaping ([String: Any]) -> Void) {
        self.config = config
        self.onEvent = onEvent
    }

    /// Initialise WhisperKit asynchronously. Posts a `transcriber-ready` event
    /// on success or a `transcriber-error` event on failure.
    func bootstrap() {
        FileHandle.standardError.write(Data("[transcriber] bootstrap begin model=\(config.modelName)\n".utf8))
        onEvent([
            "type": "transcriber_loading",
            "model": config.modelName,
        ])
        Task.detached { [weak self] in
            guard let self = self else { return }
            do {
                FileHandle.standardError.write(Data("[transcriber] WhisperKit init start\n".utf8))
                let kitConfig = WhisperKitConfig(
                    model: self.config.modelName,
                    verbose: false,
                    logLevel: .none,
                    prewarm: false,
                    load: true,
                    download: true,
                )
                let kit = try await WhisperKit(kitConfig)
                FileHandle.standardError.write(Data("[transcriber] WhisperKit init done\n".utf8))
                self.whisperKit = kit
                self.ready = true
                self.onEvent([
                    "type": "transcriber_ready",
                    "model": self.config.modelName,
                ])
                self.startWorkLoop()
            } catch {
                self.initError = "\(error)"
                FileHandle.standardError.write(Data("[transcriber] init failed: \(error)\n".utf8))
                self.onEvent([
                    "type": "transcriber_error",
                    "msg": "WhisperKit init failed: \(error)",
                ])
            }
        }
    }

    /// Append Float32 mono samples (already at the configured sample rate).
    func append(samples: UnsafePointer<Float>, count: Int) {
        ringLock.lock()
        defer { ringLock.unlock() }
        let maxSamples = Int(config.windowSeconds * config.sampleRate)
        ring.append(contentsOf: UnsafeBufferPointer(start: samples, count: count))
        totalSamples &+= count
        if ring.count > maxSamples {
            ring.removeFirst(ring.count - maxSamples)
        }
    }

    /// Signals the worker to stop after a final flush. Safe to call from any
    /// thread. Waits synchronously (via a dispatch semaphore) for the flush so
    /// the caller can be sure every captured sample has been offered to
    /// WhisperKit before the process exits.
    func stop() {
        stopFlag = true
        if let sema = self.finalFlushDone {
            _ = sema.wait(timeout: .now() + .seconds(8))
        }
    }

    private func startWorkLoop() {
        let sema = DispatchSemaphore(value: 0)
        self.finalFlushDone = sema
        workerTask = Task.detached { [weak self] in
            guard let self = self else { sema.signal(); return }
            while !self.stopFlag {
                try? await Task.sleep(nanoseconds: UInt64(self.config.stepSeconds * 1_000_000_000))
                if self.stopFlag { break }
                await self.runTranscriptionStep()
            }
            // Final flush — always run regardless of cancellation.
            await self.runTranscriptionStep(force: true)
            sema.signal()
        }
    }

    private func runTranscriptionStep(force: Bool = false) async {
        guard let kit = whisperKit else { return }

        var snapshot: [Float] = []
        var snapshotEndSample = 0
        ringLock.lock()
        if !force {
            let newSamples = totalSamples - lastTranscribedSampleCount
            if newSamples < Int(config.minNewAudioSeconds * config.sampleRate) {
                ringLock.unlock()
                return
            }
        }
        snapshot = ring
        snapshotEndSample = totalSamples
        ringLock.unlock()

        if snapshot.isEmpty { return }

        do {
            let results = try await kit.transcribe(audioArray: snapshot)
            let text = results
                .map { $0.text.trimmingCharacters(in: .whitespacesAndNewlines) }
                .joined(separator: " ")
                .trimmingCharacters(in: .whitespacesAndNewlines)
            if text.isEmpty {
                lastTranscribedSampleCount = snapshotEndSample
                return
            }
            // Diff against previously emitted text. The longest common prefix
            // becomes "final"; the tail becomes a single "partial".
            let stablePrefix = longestCommonPrefix(emittedFinalText, text)
            // We only treat the prefix as final once it has stabilised — i.e.
            // it appeared in the previous window AND survived this one.
            if stablePrefix.count > emittedFinalText.count {
                let newFinal = String(stablePrefix.dropFirst(emittedFinalText.count))
                if !newFinal.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    onEvent([
                        "type": "final",
                        "text": newFinal,
                        "session_sample_offset": lastTranscribedSampleCount,
                    ])
                    emittedFinalText = stablePrefix
                }
            }
            // Emit the trailing tail as the current partial.
            let tail = text.dropFirst(emittedFinalText.count)
            if !tail.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                onEvent([
                    "type": "partial",
                    "text": String(tail),
                    "session_sample_offset": snapshotEndSample,
                ])
            }
            lastTranscribedSampleCount = snapshotEndSample
        } catch {
            onEvent([
                "type": "transcriber_error",
                "msg": "transcribe step failed: \(error)",
            ])
        }
    }

    private func longestCommonPrefix(_ a: String, _ b: String) -> String {
        var idx = a.startIndex
        var jdx = b.startIndex
        while idx < a.endIndex, jdx < b.endIndex, a[idx] == b[jdx] {
            idx = a.index(after: idx)
            jdx = b.index(after: jdx)
        }
        return String(a[a.startIndex ..< idx])
    }
}
