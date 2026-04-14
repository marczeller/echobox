import AVFoundation
import CoreAudio
import AudioToolbox
import Darwin
import Foundation

// MARK: - CLI

struct CLIOptions {
    var sessionDir: URL
    var source: String          // "default-input" | "process-tap" | "test-signal"
    var sampleRate: Double
    var channels: UInt32
    var deviceNameSubstring: String?
    var heartbeatInterval: TimeInterval
    var levelInterval: TimeInterval
    var emitLiveTranscript: Bool
    var whisperKitModel: String

    static func parse(_ argv: [String]) -> CLIOptions {
        var sessionDir: URL?
        var source = "default-input"
        var sampleRate: Double = 16000
        var channels: UInt32 = 1
        var deviceName: String?
        var heartbeat: TimeInterval = 1.0
        var level: TimeInterval = 0.1
        var live = false
        var whisperModel = "openai_whisper-tiny"

        var i = 1
        while i < argv.count {
            let arg = argv[i]
            func next() -> String {
                i += 1
                guard i < argv.count else { fail("missing value for \(arg)") }
                return argv[i]
            }
            switch arg {
            case "--session-dir":
                sessionDir = URL(fileURLWithPath: (next() as NSString).expandingTildeInPath)
            case "--source":
                source = next()
            case "--sample-rate":
                sampleRate = Double(next()) ?? 16000
            case "--channels":
                channels = UInt32(next()) ?? 1
            case "--device-name":
                deviceName = next()
            case "--heartbeat":
                heartbeat = TimeInterval(next()) ?? 1.0
            case "--level-interval":
                level = TimeInterval(next()) ?? 0.1
            case "--live-transcript":
                live = true
            case "--whisperkit-model":
                whisperModel = next()
            case "--help", "-h":
                printHelp()
                exit(0)
            default:
                fail("unknown argument: \(arg)")
            }
            i += 1
        }
        guard let dir = sessionDir else { fail("--session-dir is required") }
        return CLIOptions(
            sessionDir: dir,
            source: source,
            sampleRate: sampleRate,
            channels: channels,
            deviceNameSubstring: deviceName,
            heartbeatInterval: heartbeat,
            levelInterval: level,
            emitLiveTranscript: live,
            whisperKitModel: whisperModel,
        )
    }

    static func printHelp() {
        let msg = """
        echobox-capture — session-oriented audio capture helper for echobox

        Usage:
          echobox-capture --session-dir <path> [options]

        Options:
          --session-dir <path>     Directory to write session.json and audio/mic.wav (required)
          --source <kind>          Capture source: default-input (default), process-tap
          --sample-rate <hz>       Output sample rate (default: 16000)
          --channels <n>           Output channel count (default: 1)
          --device-name <substr>   For default-input: pick the first input device
                                   whose name contains this substring (case-insensitive)
          --heartbeat <sec>        Heartbeat interval in seconds (default: 1.0)
          --level-interval <sec>   Level-meter interval in seconds (default: 0.1)
          --live-transcript        Enable WhisperKit streaming (emits partial/final events)
          --whisperkit-model <id>  WhisperKit model identifier (default: openai_whisper-tiny)
                                   Examples: openai_whisper-tiny, openai_whisper-base,
                                             openai_whisper-large-v3-v20240930_turbo
          --help                   Show this help

        stdout: one JSONL event per line
        stderr: human-readable log lines
        """
        FileHandle.standardError.write(Data((msg + "\n").utf8))
    }
}

func fail(_ msg: String) -> Never {
    FileHandle.standardError.write(Data("echobox-capture: \(msg)\n".utf8))
    exit(2)
}

// MARK: - Logging + JSONL event stream

func logStderr(_ msg: String) {
    FileHandle.standardError.write(Data("[echobox-capture] \(msg)\n".utf8))
}

let stdoutLock = NSLock()

func emit(_ event: [String: Any]) {
    var payload = event
    if payload["ts"] == nil {
        payload["ts"] = Date().timeIntervalSince1970
    }
    guard
        let data = try? JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])
    else {
        return
    }
    stdoutLock.lock()
    defer { stdoutLock.unlock() }
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data("\n".utf8))
    try? FileHandle.standardOutput.synchronize()
}

// MARK: - WAV writer (16-bit PCM, little-endian)

final class WavWriter {
    private let handle: FileHandle
    private let url: URL
    private let sampleRate: UInt32
    private let channels: UInt16
    private let bitsPerSample: UInt16 = 16
    private var dataBytes: UInt32 = 0
    private let lock = NSLock()

    var framesWritten: UInt64 {
        let bytesPerFrame = UInt64(channels) * UInt64(bitsPerSample / 8)
        return bytesPerFrame == 0 ? 0 : UInt64(dataBytes) / bytesPerFrame
    }

    init(url: URL, sampleRate: UInt32, channels: UInt16) throws {
        self.url = url
        self.sampleRate = sampleRate
        self.channels = channels
        try FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(), withIntermediateDirectories: true
        )
        FileManager.default.createFile(atPath: url.path, contents: nil)
        self.handle = try FileHandle(forWritingTo: url)
        try self.writePlaceholderHeader()
    }

    private func writePlaceholderHeader() throws {
        var header = Data()
        header.append(contentsOf: "RIFF".utf8)
        header.append(uint32LE(0))
        header.append(contentsOf: "WAVE".utf8)
        header.append(contentsOf: "fmt ".utf8)
        header.append(uint32LE(16))
        header.append(uint16LE(1))
        header.append(uint16LE(channels))
        header.append(uint32LE(sampleRate))
        let byteRate = sampleRate * UInt32(channels) * UInt32(bitsPerSample / 8)
        header.append(uint32LE(byteRate))
        header.append(uint16LE(channels * (bitsPerSample / 8)))
        header.append(uint16LE(bitsPerSample))
        header.append(contentsOf: "data".utf8)
        header.append(uint32LE(0))
        try handle.write(contentsOf: header)
    }

    func appendInt16(_ buffer: UnsafePointer<Int16>, frames: Int) {
        let byteCount = frames * Int(channels) * Int(bitsPerSample / 8)
        let data = Data(bytes: buffer, count: byteCount)
        lock.lock()
        defer { lock.unlock() }
        do {
            try handle.write(contentsOf: data)
            dataBytes &+= UInt32(byteCount)
        } catch {
            logStderr("wav append failed: \(error)")
        }
    }

    func appendFloat32(_ buffer: UnsafePointer<Float>, frames: Int) {
        let count = frames * Int(channels)
        var ints = [Int16](repeating: 0, count: count)
        for i in 0 ..< count {
            let f = max(-1.0, min(1.0, buffer[i]))
            ints[i] = Int16(f * Float(Int16.max))
        }
        ints.withUnsafeBufferPointer { ptr in
            if let base = ptr.baseAddress {
                self.appendInt16(base, frames: frames)
            }
        }
    }

    func close() {
        lock.lock()
        defer { lock.unlock() }
        do {
            try handle.seek(toOffset: 4)
            try handle.write(contentsOf: uint32LE(36 + dataBytes))
            try handle.seek(toOffset: 40)
            try handle.write(contentsOf: uint32LE(dataBytes))
            try handle.close()
        } catch {
            logStderr("wav close failed: \(error)")
        }
    }

    private func uint32LE(_ v: UInt32) -> Data {
        var le = v.littleEndian
        return withUnsafeBytes(of: &le) { Data($0) }
    }

    private func uint16LE(_ v: UInt16) -> Data {
        var le = v.littleEndian
        return withUnsafeBytes(of: &le) { Data($0) }
    }
}

// MARK: - CoreAudio device lookup

func defaultInputDeviceID() -> AudioObjectID {
    var addr = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyDefaultInputDevice,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain
    )
    var dev: AudioObjectID = 0
    var size = UInt32(MemoryLayout<AudioObjectID>.size)
    let status = AudioObjectGetPropertyData(
        AudioObjectID(kAudioObjectSystemObject),
        &addr, 0, nil, &size, &dev
    )
    if status != noErr {
        logStderr("default input device lookup failed: \(status)")
        return 0
    }
    return dev
}

func findInputDevice(nameContains needle: String) -> AudioObjectID? {
    var addr = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyDevices,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain
    )
    var size: UInt32 = 0
    guard
        AudioObjectGetPropertyDataSize(
            AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size
        ) == noErr
    else {
        return nil
    }
    let count = Int(size) / MemoryLayout<AudioObjectID>.size
    var ids = [AudioObjectID](repeating: 0, count: count)
    guard
        ids.withUnsafeMutableBufferPointer({ buf -> OSStatus in
            AudioObjectGetPropertyData(
                AudioObjectID(kAudioObjectSystemObject),
                &addr, 0, nil, &size, buf.baseAddress!
            )
        }) == noErr
    else {
        return nil
    }
    let needleLower = needle.lowercased()
    for id in ids {
        if deviceHasInputStreams(id), let name = deviceName(id),
           name.lowercased().contains(needleLower) {
            return id
        }
    }
    return nil
}

func deviceName(_ id: AudioObjectID) -> String? {
    var addr = AudioObjectPropertyAddress(
        mSelector: kAudioObjectPropertyName,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain
    )
    var size = UInt32(MemoryLayout<CFString?>.size)
    var cfName: Unmanaged<CFString>?
    let status = withUnsafeMutablePointer(to: &cfName) { ptr -> OSStatus in
        ptr.withMemoryRebound(to: UInt8.self, capacity: Int(size)) { raw in
            AudioObjectGetPropertyData(id, &addr, 0, nil, &size, raw)
        }
    }
    guard status == noErr, let cf = cfName?.takeRetainedValue() else { return nil }
    return cf as String
}

func deviceUID(_ id: AudioObjectID) -> String? {
    var addr = AudioObjectPropertyAddress(
        mSelector: kAudioDevicePropertyDeviceUID,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain
    )
    var size = UInt32(MemoryLayout<CFString?>.size)
    var cfUID: Unmanaged<CFString>?
    let status = withUnsafeMutablePointer(to: &cfUID) { ptr -> OSStatus in
        ptr.withMemoryRebound(to: UInt8.self, capacity: Int(size)) { raw in
            AudioObjectGetPropertyData(id, &addr, 0, nil, &size, raw)
        }
    }
    guard status == noErr, let cf = cfUID?.takeRetainedValue() else { return nil }
    return cf as String
}

func deviceHasInputStreams(_ id: AudioObjectID) -> Bool {
    var addr = AudioObjectPropertyAddress(
        mSelector: kAudioDevicePropertyStreams,
        mScope: kAudioDevicePropertyScopeInput,
        mElement: kAudioObjectPropertyElementMain
    )
    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(id, &addr, 0, nil, &size) == noErr else { return false }
    return size >= UInt32(MemoryLayout<AudioStreamID>.size)
}

func setDefaultInputDevice(_ id: AudioObjectID) -> Bool {
    var addr = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyDefaultInputDevice,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain
    )
    var dev = id
    let status = AudioObjectSetPropertyData(
        AudioObjectID(kAudioObjectSystemObject),
        &addr, 0, nil, UInt32(MemoryLayout<AudioObjectID>.size), &dev
    )
    return status == noErr
}

// MARK: - Process-tap setup (macOS 14.2+)

@available(macOS 14.2, *)
func createProcessTapAggregate() -> (tap: AudioObjectID, aggregate: AudioObjectID)? {
    let tapDesc = CATapDescription(monoGlobalTapButExcludeProcesses: [])
    tapDesc.name = "echobox-capture-tap"
    let tapUUID = UUID()
    tapDesc.uuid = tapUUID
    tapDesc.muteBehavior = .unmuted

    var tapID: AudioObjectID = 0
    guard AudioHardwareCreateProcessTap(tapDesc, &tapID) == noErr else {
        logStderr("AudioHardwareCreateProcessTap failed (TCC permission?)")
        return nil
    }
    let aggDict: [String: Any] = [
        "uid": UUID().uuidString,
        "name": "Echobox Capture Aggregate",
        "private": 1,
        "stacked": 0,
        "taps": [["uid": tapUUID.uuidString, "drift": 1] as [String: Any]],
        "tapautostart": 1,
    ]
    var aggID: AudioObjectID = 0
    guard AudioHardwareCreateAggregateDevice(aggDict as CFDictionary, &aggID) == noErr else {
        logStderr("AudioHardwareCreateAggregateDevice failed")
        AudioHardwareDestroyProcessTap(tapID)
        return nil
    }
    return (tapID, aggID)
}

// MARK: - Capture engine (AVAudioEngine path)

final class CaptureEngine {
    let opts: CLIOptions
    let wavURL: URL
    let sessionID: String
    let engine = AVAudioEngine()
    var converter: AVAudioConverter?
    var wav: WavWriter?
    var transcriber: StreamingTranscriber?

    // Raw HAL path (process-tap source)
    var halUnit: AudioComponentInstance?
    var halTapID: AudioObjectID = 0
    var halAggregateID: AudioObjectID = 0
    var halNativeSampleRate: Double = 16_000
    var halNativeChannels: UInt32 = 1
    var halConverter: AVAudioConverter?
    var halInputFormat: AVAudioFormat?
    var halOutputFormat: AVAudioFormat?

    // Metrics
    private let metricsLock = NSLock()
    private var totalFramesWritten: UInt64 = 0
    private var levelAccum: Double = 0
    private var levelSampleCount: UInt64 = 0
    private let startedAt = Date()
    private let stopFlag = AtomicBool(true)

    init(_ opts: CLIOptions) {
        self.opts = opts
        let name = opts.sessionDir.lastPathComponent
        self.sessionID = name.isEmpty ? UUID().uuidString : name
        self.wavURL = opts.sessionDir.appendingPathComponent("audio/mic.wav")
    }

    func run() -> Int32 {
        do {
            try writeSessionJson(captureStatus: "recording")
        } catch {
            emit(["type": "error", "msg": "session.json write failed: \(error)"])
            return 1
        }

        // Optional: pin default input to the named device (e.g. BlackHole).
        if opts.source == "default-input", let needle = opts.deviceNameSubstring {
            if let id = findInputDevice(nameContains: needle) {
                if setDefaultInputDevice(id) {
                    logStderr("set default input to '\(deviceName(id) ?? "?")' (id=\(id))")
                } else {
                    logStderr("WARNING: failed to pin default input to '\(needle)'")
                }
            } else {
                logStderr("WARNING: no input device matched '\(needle)' — using system default")
            }
        }

        if opts.source == "test-signal" {
            return runTestSignal()
        }
        if opts.source == "process-tap" {
            if #available(macOS 14.2, *) {
                return runProcessTap()
            } else {
                emit(["type": "error", "msg": "process-tap requires macOS 14.2+"])
                return 1
            }
        }
        guard opts.source == "default-input" else {
            emit(["type": "error", "msg": "unknown source: \(opts.source)"])
            return 1
        }

        let inputNode = engine.inputNode
        let inputFormat = inputNode.inputFormat(forBus: 0)
        logStderr("input format: \(inputFormat.sampleRate) Hz, \(inputFormat.channelCount) ch")

        guard inputFormat.sampleRate > 0 else {
            emit(["type": "error", "msg": "input device has zero sample rate (no mic permission?)"])
            return 1
        }

        guard
            let targetFormat = AVAudioFormat(
                commonFormat: .pcmFormatFloat32,
                sampleRate: opts.sampleRate,
                channels: AVAudioChannelCount(opts.channels),
                interleaved: false
            )
        else {
            emit(["type": "error", "msg": "target format creation failed"])
            return 1
        }

        self.converter = AVAudioConverter(from: inputFormat, to: targetFormat)
        if converter == nil {
            emit(["type": "error", "msg": "cannot create converter from input \(inputFormat) to target \(targetFormat)"])
            return 1
        }

        do {
            self.wav = try WavWriter(
                url: wavURL,
                sampleRate: UInt32(opts.sampleRate),
                channels: UInt16(opts.channels),
            )
        } catch {
            emit(["type": "error", "msg": "wav open failed: \(error)"])
            return 1
        }

        if opts.emitLiveTranscript {
            let transcriberConfig = StreamingTranscriber.Config(
                modelName: opts.whisperKitModel,
                windowSeconds: 12.0,
                stepSeconds: 0.75,
                minNewAudioSeconds: 0.5,
                sampleRate: opts.sampleRate,
            )
            self.transcriber = StreamingTranscriber(config: transcriberConfig) { event in
                emit(event)
            }
            self.transcriber?.bootstrap()
        }

        inputNode.installTap(
            onBus: 0,
            bufferSize: 1024,
            format: inputFormat
        ) { [weak self] buffer, _ in
            self?.process(buffer: buffer, targetFormat: targetFormat)
        }

        do {
            try engine.start()
        } catch {
            emit(["type": "error", "msg": "engine start failed: \(error)"])
            return 1
        }

        installSignalHandlers()
        emit([
            "type": "started",
            "session_id": sessionID,
            "source": opts.source,
            "sample_rate": opts.sampleRate,
            "channels": opts.channels,
            "wav_path": wavURL.path,
            "device_format_sample_rate": inputFormat.sampleRate,
            "device_format_channels": inputFormat.channelCount,
        ])

        let heartbeatTimer = DispatchSource.makeTimerSource(queue: .global())
        heartbeatTimer.schedule(deadline: .now() + opts.heartbeatInterval, repeating: opts.heartbeatInterval)
        heartbeatTimer.setEventHandler { [weak self] in self?.emitHeartbeat() }
        heartbeatTimer.resume()

        let levelTimer = DispatchSource.makeTimerSource(queue: .global())
        levelTimer.schedule(deadline: .now() + opts.levelInterval, repeating: opts.levelInterval)
        levelTimer.setEventHandler { [weak self] in self?.emitLevel() }
        levelTimer.resume()

        while stopFlag.value {
            Thread.sleep(forTimeInterval: 0.05)
        }
        heartbeatTimer.cancel()
        levelTimer.cancel()

        inputNode.removeTap(onBus: 0)
        engine.stop()
        transcriber?.stop()
        wav?.close()

        let duration = Date().timeIntervalSince(startedAt)
        try? writeSessionJson(captureStatus: "completed", endedAt: Date(), durationSeconds: duration)

        emit([
            "type": "stopped",
            "frames_written": totalFramesWritten,
            "duration_seconds": duration,
        ])
        return 0
    }

    /// Shared sample sink used by the AVAudioEngine path, the test-signal
    /// path, and the raw-HAL process-tap path. Expects Float32 mono samples at
    /// `opts.sampleRate`.
    fileprivate func ingestFloat32(_ samples: UnsafePointer<Float>, frames: Int) {
        wav?.appendFloat32(samples, frames: frames)
        transcriber?.append(samples: samples, count: frames)
        var sumSq: Double = 0
        let count = frames * Int(opts.channels)
        for i in 0 ..< count {
            let s = Double(samples[i])
            sumSq += s * s
        }
        metricsLock.lock()
        totalFramesWritten &+= UInt64(frames)
        levelAccum += sumSq
        levelSampleCount &+= UInt64(count)
        metricsLock.unlock()
    }

    // MARK: - Process-tap path (raw HAL)

    @available(macOS 14.2, *)
    private func runProcessTap() -> Int32 {
        guard let pair = createProcessTapAggregate() else {
            emit(["type": "error", "msg": "process tap setup failed (check TCC permission for System Audio Recording)"])
            return 1
        }
        self.halTapID = pair.tap
        self.halAggregateID = pair.aggregate

        // Query native format of the aggregate so we know what the HAL will deliver.
        var fmtAddr = AudioObjectPropertyAddress(
            mSelector: kAudioDevicePropertyStreamFormat,
            mScope: kAudioDevicePropertyScopeInput,
            mElement: kAudioObjectPropertyElementMain
        )
        var nativeFmt = AudioStreamBasicDescription()
        var nativeFmtSize = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
        let nativeStatus = AudioObjectGetPropertyData(
            halAggregateID, &fmtAddr, 0, nil, &nativeFmtSize, &nativeFmt
        )
        if nativeStatus == noErr {
            halNativeSampleRate = nativeFmt.mSampleRate > 0 ? nativeFmt.mSampleRate : opts.sampleRate
            halNativeChannels = nativeFmt.mChannelsPerFrame > 0 ? nativeFmt.mChannelsPerFrame : 1
        }
        logStderr("process-tap native format: \(halNativeSampleRate) Hz, \(halNativeChannels) ch")

        // Build an AVAudioConverter for native→target resample + downmix.
        // This carries resampling state across render-callback boundaries, so
        // we don't lose fractional samples or drift over long calls.
        guard
            let inFmt = AVAudioFormat(
                commonFormat: .pcmFormatFloat32,
                sampleRate: halNativeSampleRate,
                channels: AVAudioChannelCount(halNativeChannels),
                interleaved: true,
            ),
            let outFmt = AVAudioFormat(
                commonFormat: .pcmFormatFloat32,
                sampleRate: opts.sampleRate,
                channels: AVAudioChannelCount(opts.channels),
                interleaved: false,
            )
        else {
            emit(["type": "error", "msg": "process-tap format creation failed"])
            return 1
        }
        guard let conv = AVAudioConverter(from: inFmt, to: outFmt) else {
            emit(["type": "error", "msg": "process-tap converter creation failed"])
            return 1
        }
        conv.sampleRateConverterAlgorithm = AVSampleRateConverterAlgorithm_Normal
        conv.sampleRateConverterQuality = AVAudioQuality.medium.rawValue
        self.halInputFormat = inFmt
        self.halOutputFormat = outFmt
        self.halConverter = conv

        do {
            self.wav = try WavWriter(
                url: wavURL,
                sampleRate: UInt32(opts.sampleRate),
                channels: UInt16(opts.channels),
            )
        } catch {
            emit(["type": "error", "msg": "wav open failed: \(error)"])
            return 1
        }

        if opts.emitLiveTranscript {
            let transcriberConfig = StreamingTranscriber.Config(
                modelName: opts.whisperKitModel,
                windowSeconds: 12.0,
                stepSeconds: 0.75,
                minNewAudioSeconds: 0.5,
                sampleRate: opts.sampleRate,
            )
            self.transcriber = StreamingTranscriber(config: transcriberConfig) { event in
                emit(event)
            }
            self.transcriber?.bootstrap()
        }

        do {
            try setupHALInputUnit(device: halAggregateID)
        } catch {
            emit(["type": "error", "msg": "HAL audio unit setup failed: \(error)"])
            return 1
        }

        installSignalHandlers()
        emit([
            "type": "started",
            "session_id": sessionID,
            "source": opts.source,
            "sample_rate": opts.sampleRate,
            "channels": opts.channels,
            "wav_path": wavURL.path,
            "device_format_sample_rate": halNativeSampleRate,
            "device_format_channels": halNativeChannels,
        ])

        guard let au = halUnit else {
            emit(["type": "error", "msg": "HAL unit nil"])
            return 1
        }
        let startStatus = AudioOutputUnitStart(au)
        if startStatus != noErr {
            emit(["type": "error", "msg": "AudioOutputUnitStart failed: \(startStatus)"])
            return 1
        }

        let heartbeatTimer = DispatchSource.makeTimerSource(queue: .global())
        heartbeatTimer.schedule(deadline: .now() + opts.heartbeatInterval, repeating: opts.heartbeatInterval)
        heartbeatTimer.setEventHandler { [weak self] in self?.emitHeartbeat() }
        heartbeatTimer.resume()
        let levelTimer = DispatchSource.makeTimerSource(queue: .global())
        levelTimer.schedule(deadline: .now() + opts.levelInterval, repeating: opts.levelInterval)
        levelTimer.setEventHandler { [weak self] in self?.emitLevel() }
        levelTimer.resume()

        while stopFlag.value {
            Thread.sleep(forTimeInterval: 0.05)
        }
        heartbeatTimer.cancel()
        levelTimer.cancel()
        transcriber?.stop()

        AudioOutputUnitStop(au)
        AudioComponentInstanceDispose(au)
        self.halUnit = nil
        AudioHardwareDestroyAggregateDevice(halAggregateID)
        AudioHardwareDestroyProcessTap(halTapID)
        wav?.close()

        let duration = Date().timeIntervalSince(startedAt)
        try? writeSessionJson(captureStatus: "completed", endedAt: Date(), durationSeconds: duration)
        emit([
            "type": "stopped",
            "frames_written": totalFramesWritten,
            "duration_seconds": duration,
        ])
        return 0
    }

    private func setupHALInputUnit(device: AudioObjectID) throws {
        var desc = AudioComponentDescription(
            componentType: kAudioUnitType_Output,
            componentSubType: kAudioUnitSubType_HALOutput,
            componentManufacturer: kAudioUnitManufacturer_Apple,
            componentFlags: 0,
            componentFlagsMask: 0
        )
        guard let component = AudioComponentFindNext(nil, &desc) else {
            throw NSError(domain: "echobox-capture", code: 10, userInfo: [NSLocalizedDescriptionKey: "no HALOutput component"])
        }
        var unit: AudioComponentInstance?
        try check(AudioComponentInstanceNew(component, &unit), "AudioComponentInstanceNew (HAL)")
        guard let au = unit else {
            throw NSError(domain: "echobox-capture", code: 11, userInfo: [NSLocalizedDescriptionKey: "component instance nil (HAL)"])
        }
        var one: UInt32 = 1
        var zero: UInt32 = 0
        try check(
            AudioUnitSetProperty(
                au, kAudioOutputUnitProperty_EnableIO, kAudioUnitScope_Input, 1,
                &one, UInt32(MemoryLayout<UInt32>.size),
            ), "enable input (HAL)"
        )
        try check(
            AudioUnitSetProperty(
                au, kAudioOutputUnitProperty_EnableIO, kAudioUnitScope_Output, 0,
                &zero, UInt32(MemoryLayout<UInt32>.size),
            ), "disable output (HAL)"
        )
        var devID = device
        try check(
            AudioUnitSetProperty(
                au, kAudioOutputUnitProperty_CurrentDevice, kAudioUnitScope_Global, 0,
                &devID, UInt32(MemoryLayout<AudioObjectID>.size),
            ), "set current device (HAL)"
        )
        // Request Float32 interleaved at the device's native rate on the output
        // side of bus 1. The HAL will convert the input from whatever the
        // device provides. We then resample+downmix to opts.sampleRate/channels
        // ourselves in the render callback.
        var fmt = AudioStreamBasicDescription(
            mSampleRate: halNativeSampleRate,
            mFormatID: kAudioFormatLinearPCM,
            mFormatFlags: kAudioFormatFlagIsFloat | kAudioFormatFlagIsPacked,
            mBytesPerPacket: 4 * halNativeChannels,
            mFramesPerPacket: 1,
            mBytesPerFrame: 4 * halNativeChannels,
            mChannelsPerFrame: halNativeChannels,
            mBitsPerChannel: 32,
            mReserved: 0,
        )
        try check(
            AudioUnitSetProperty(
                au, kAudioUnitProperty_StreamFormat, kAudioUnitScope_Output, 1,
                &fmt, UInt32(MemoryLayout<AudioStreamBasicDescription>.size),
            ), "set HAL output format (bus1)"
        )
        var cb = AURenderCallbackStruct(
            inputProc: halInputCallback,
            inputProcRefCon: UnsafeMutableRawPointer(Unmanaged.passUnretained(self).toOpaque()),
        )
        try check(
            AudioUnitSetProperty(
                au, kAudioOutputUnitProperty_SetInputCallback, kAudioUnitScope_Global, 0,
                &cb, UInt32(MemoryLayout<AURenderCallbackStruct>.size),
            ), "set HAL input callback"
        )
        try check(AudioUnitInitialize(au), "AudioUnitInitialize (HAL)")
        self.halUnit = au
    }

    private static var halRenderCallbackCount: UInt64 = 0

    fileprivate func handleHALRender(
        ioActionFlags: UnsafeMutablePointer<AudioUnitRenderActionFlags>,
        inTimeStamp: UnsafePointer<AudioTimeStamp>,
        inNumberFrames: UInt32,
    ) -> OSStatus {
        guard
            let au = halUnit,
            let inFmt = halInputFormat,
            let outFmt = halOutputFormat,
            let conv = halConverter
        else {
            return noErr
        }
        Self.halRenderCallbackCount += 1
        if Self.halRenderCallbackCount == 1 {
            logStderr("HAL render callback fired (frames=\(inNumberFrames))")
        }
        // Pull native samples from the HAL unit into an AVAudioPCMBuffer.
        guard
            let inBuf = AVAudioPCMBuffer(pcmFormat: inFmt, frameCapacity: inNumberFrames)
        else {
            return noErr
        }
        inBuf.frameLength = inNumberFrames
        let abl = inBuf.audioBufferList
        let status = AudioUnitRender(au, ioActionFlags, inTimeStamp, 1, inNumberFrames, UnsafeMutablePointer(mutating: abl))
        if status != noErr { return status }
        // Convert with AVAudioConverter. It maintains resampling state across
        // calls, so there is no per-callback fractional-sample loss.
        let ratio = outFmt.sampleRate / inFmt.sampleRate
        let outCapacity = AVAudioFrameCount(Double(inNumberFrames) * ratio + 64)
        guard
            let outBuf = AVAudioPCMBuffer(pcmFormat: outFmt, frameCapacity: outCapacity)
        else {
            return noErr
        }
        var supplied = false
        let block: AVAudioConverterInputBlock = { _, outStatus in
            if supplied {
                outStatus.pointee = .noDataNow
                return nil
            }
            supplied = true
            outStatus.pointee = .haveData
            return inBuf
        }
        var err: NSError?
        let convStatus = conv.convert(to: outBuf, error: &err, withInputFrom: block)
        if convStatus == .error {
            if let err = err { logStderr("hal converter error: \(err)") }
            return noErr
        }
        if let floatData = outBuf.floatChannelData?[0] {
            self.ingestFloat32(floatData, frames: Int(outBuf.frameLength))
        }
        return noErr
    }

    private func runTestSignal() -> Int32 {
        do {
            self.wav = try WavWriter(
                url: wavURL,
                sampleRate: UInt32(opts.sampleRate),
                channels: UInt16(opts.channels),
            )
        } catch {
            emit(["type": "error", "msg": "wav open failed: \(error)"])
            return 1
        }
        if opts.emitLiveTranscript {
            let transcriberConfig = StreamingTranscriber.Config(
                modelName: opts.whisperKitModel,
                windowSeconds: 12.0,
                stepSeconds: 0.75,
                minNewAudioSeconds: 0.5,
                sampleRate: opts.sampleRate,
            )
            self.transcriber = StreamingTranscriber(config: transcriberConfig) { event in
                emit(event)
            }
            self.transcriber?.bootstrap()
        }
        installSignalHandlers()
        emit([
            "type": "started",
            "session_id": sessionID,
            "source": opts.source,
            "sample_rate": opts.sampleRate,
            "channels": opts.channels,
            "wav_path": wavURL.path,
        ])

        let heartbeatTimer = DispatchSource.makeTimerSource(queue: .global())
        heartbeatTimer.schedule(deadline: .now() + opts.heartbeatInterval, repeating: opts.heartbeatInterval)
        heartbeatTimer.setEventHandler { [weak self] in self?.emitHeartbeat() }
        heartbeatTimer.resume()
        let levelTimer = DispatchSource.makeTimerSource(queue: .global())
        levelTimer.schedule(deadline: .now() + opts.levelInterval, repeating: opts.levelInterval)
        levelTimer.setEventHandler { [weak self] in self?.emitLevel() }
        levelTimer.resume()

        let chunkFrames = 1024
        var phase: Double = 0
        let phaseInc = 2.0 * Double.pi * 440.0 / opts.sampleRate
        var buffer = [Int16](repeating: 0, count: chunkFrames * Int(opts.channels))
        let frameDuration = Double(chunkFrames) / opts.sampleRate

        while stopFlag.value {
            for i in 0 ..< chunkFrames {
                let sample = Int16(Double(Int16.max) * 0.3 * sin(phase))
                phase += phaseInc
                if phase > 2.0 * Double.pi { phase -= 2.0 * Double.pi }
                for c in 0 ..< Int(opts.channels) {
                    buffer[i * Int(opts.channels) + c] = sample
                }
            }
            buffer.withUnsafeBufferPointer { ptr in
                if let base = ptr.baseAddress {
                    wav?.appendInt16(base, frames: chunkFrames)
                }
            }
            if transcriber != nil {
                var floats = [Float](repeating: 0, count: buffer.count)
                for i in 0 ..< buffer.count {
                    floats[i] = Float(buffer[i]) / Float(Int16.max)
                }
                floats.withUnsafeBufferPointer { ptr in
                    if let base = ptr.baseAddress {
                        transcriber?.append(samples: base, count: chunkFrames)
                    }
                }
            }
            var sumSq: Double = 0
            for s in buffer {
                let x = Double(s) / Double(Int16.max)
                sumSq += x * x
            }
            metricsLock.lock()
            totalFramesWritten &+= UInt64(chunkFrames)
            levelAccum += sumSq
            levelSampleCount &+= UInt64(buffer.count)
            metricsLock.unlock()
            Thread.sleep(forTimeInterval: frameDuration)
        }
        heartbeatTimer.cancel()
        levelTimer.cancel()
        transcriber?.stop()
        wav?.close()

        let duration = Date().timeIntervalSince(startedAt)
        try? writeSessionJson(captureStatus: "completed", endedAt: Date(), durationSeconds: duration)
        emit([
            "type": "stopped",
            "frames_written": totalFramesWritten,
            "duration_seconds": duration,
        ])
        return 0
    }

    private func process(buffer: AVAudioPCMBuffer, targetFormat: AVAudioFormat) {
        guard let converter = converter else { return }
        let ratio = targetFormat.sampleRate / buffer.format.sampleRate
        let outCapacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio + 32)
        guard
            let out = AVAudioPCMBuffer(pcmFormat: targetFormat, frameCapacity: outCapacity)
        else {
            return
        }
        var supplied = false
        let inputBlock: AVAudioConverterInputBlock = { _, outStatus in
            if supplied {
                outStatus.pointee = .noDataNow
                return nil
            }
            supplied = true
            outStatus.pointee = .haveData
            return buffer
        }
        var error: NSError?
        let status = converter.convert(to: out, error: &error, withInputFrom: inputBlock)
        if status == .error || out.frameLength == 0 {
            if let error = error { logStderr("converter error: \(error)") }
            return
        }
        guard let floatData = out.floatChannelData?[0] else { return }
        let frames = Int(out.frameLength)

        // Write 16-bit PCM to the WAV file.
        wav?.appendFloat32(floatData, frames: frames)

        // Feed the live transcriber if enabled.
        transcriber?.append(samples: floatData, count: frames)

        // RMS metric over Float32.
        var sumSq: Double = 0
        let count = frames * Int(opts.channels)
        for i in 0 ..< count {
            let s = Double(floatData[i])
            sumSq += s * s
        }
        metricsLock.lock()
        totalFramesWritten &+= UInt64(frames)
        levelAccum += sumSq
        levelSampleCount &+= UInt64(count)
        metricsLock.unlock()
    }

    private func writeSessionJson(
        captureStatus: String,
        endedAt: Date? = nil,
        durationSeconds: Double? = nil,
    ) throws {
        var sessionJson: [String: Any] = [
            "schema_version": 1,
            "session_id": sessionID,
            "started_at": ISO8601DateFormatter().string(from: startedAt),
            "sample_rate": opts.sampleRate,
            "channels": opts.channels,
            "source": opts.source,
            "capture_status": captureStatus,
        ]
        if let endedAt = endedAt {
            sessionJson["ended_at"] = ISO8601DateFormatter().string(from: endedAt)
        }
        if let duration = durationSeconds {
            sessionJson["duration_seconds"] = duration
        }
        let data = try JSONSerialization.data(
            withJSONObject: sessionJson,
            options: [.prettyPrinted, .sortedKeys],
        )
        let url = opts.sessionDir.appendingPathComponent("session.json")
        try FileManager.default.createDirectory(
            at: opts.sessionDir, withIntermediateDirectories: true,
        )
        try data.write(to: url, options: [.atomic])
    }

    private func emitHeartbeat() {
        metricsLock.lock()
        let frames = totalFramesWritten
        metricsLock.unlock()
        emit(["type": "heartbeat", "frames_written": frames])
    }

    private func emitLevel() {
        metricsLock.lock()
        var rms = 0.0
        if levelSampleCount > 0 {
            rms = sqrt(levelAccum / Double(levelSampleCount))
            levelAccum = 0
            levelSampleCount = 0
        }
        metricsLock.unlock()
        emit(["type": "level", "rms": rms])
    }

    private func installSignalHandlers() {
        sharedStopFlag = stopFlag
        let handler: @convention(c) (Int32) -> Void = { _ in
            sharedStopFlag?.value = false
        }
        signal(SIGINT, handler)
        signal(SIGTERM, handler)
    }
}

// Core-Audio render callback for the HAL process-tap path. Must be
// @convention(c) — captures nothing except the opaque self pointer.
let halInputCallback: AURenderCallback = {
    (inRefCon, ioActionFlags, inTimeStamp, _, inNumberFrames, _) -> OSStatus in
    let engine = Unmanaged<CaptureEngine>.fromOpaque(inRefCon).takeUnretainedValue()
    return engine.handleHALRender(
        ioActionFlags: ioActionFlags,
        inTimeStamp: inTimeStamp,
        inNumberFrames: inNumberFrames,
    )
}

extension CaptureEngine {
    fileprivate func check(_ status: OSStatus, _ msg: String) throws {
        if status != noErr {
            throw NSError(
                domain: "echobox-capture",
                code: Int(status),
                userInfo: [NSLocalizedDescriptionKey: "\(msg) (OSStatus=\(status))"],
            )
        }
    }
}

// Atomic flag usable from signal handlers (which can't capture context).
final class AtomicBool {
    private var _value: Int32
    init(_ v: Bool) { _value = v ? 1 : 0 }
    var value: Bool {
        get { OSAtomicAdd32(0, &_value) != 0 }
        set {
            var cur = _value
            while !OSAtomicCompareAndSwap32(cur, newValue ? 1 : 0, &_value) {
                cur = _value
            }
        }
    }
}
var sharedStopFlag: AtomicBool?

// MARK: - Entry point

let opts = CLIOptions.parse(CommandLine.arguments)
let engine = CaptureEngine(opts)
let code = engine.run()
exit(code)
