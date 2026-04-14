import CoreAudio
import AudioToolbox
import Darwin
import Foundation

// ─── Configuration ──────────────────────────────────────────────────────────
let outputSampleRate: Float64 = 16000
let outputChannels: UInt32 = 1

// ─── Globals ────────────────────────────────────────────────────────────────
var tapID: AudioObjectID = 0
var aggregateID: AudioObjectID = 0
var audioUnit: AudioComponentInstance?
var running = true

signal(SIGINT)  { _ in running = false }
signal(SIGTERM) { _ in running = false }

func check(_ status: OSStatus, _ msg: String) {
    guard status != noErr else { return }
    log("Error \(status): \(msg)")
    exit(1)
}

func log(_ msg: String) {
    FileHandle.standardError.write("\(msg)\n".data(using: .utf8)!)
}

// ─── Create process tap + aggregate device ──────────────────────────────────
@available(macOS 14.2, *)
func createTapAndAggregate() -> (AudioObjectID, AudioObjectID) {
    // 1. Create the tap
    let tapDesc = CATapDescription(monoGlobalTapButExcludeProcesses: [])
    tapDesc.name = "echobox-system-tap"
    let tapUUID = UUID()
    tapDesc.uuid = tapUUID
    tapDesc.muteBehavior = .unmuted

    var outTapID: AudioObjectID = 0
    check(AudioHardwareCreateProcessTap(tapDesc, &outTapID), "create process tap")
    log("Tap created: id=\(outTapID), uuid=\(tapUUID.uuidString)")

    // 2. Create an aggregate device that includes the tap
    let aggUID = UUID().uuidString
    let aggDict: [String: Any] = [
        "uid": aggUID,
        "name": "Echobox System Audio",
        "private": 1,
        "stacked": 0,
        "taps": [
            [
                "uid": tapUUID.uuidString,
                "drift": 1
            ] as [String: Any]
        ],
        "tapautostart": 1
    ]

    var outAggID: AudioObjectID = 0
    check(
        AudioHardwareCreateAggregateDevice(aggDict as CFDictionary, &outAggID),
        "create aggregate device"
    )
    log("Aggregate device created: id=\(outAggID)")

    return (outTapID, outAggID)
}

// ─── Set up audio unit for capture ──────────────────────────────────────────
func setupAudioUnit(device: AudioObjectID) -> AudioComponentInstance {
    var desc = AudioComponentDescription(
        componentType: kAudioUnitType_Output,
        componentSubType: kAudioUnitSubType_HALOutput,
        componentManufacturer: kAudioUnitManufacturer_Apple,
        componentFlags: 0,
        componentFlagsMask: 0
    )

    guard let component = AudioComponentFindNext(nil, &desc) else {
        log("No HALOutput component"); exit(1)
    }

    var unit: AudioComponentInstance?
    check(AudioComponentInstanceNew(component, &unit), "new audio unit")
    guard let au = unit else { exit(1) }

    // Enable input on bus 1
    var one: UInt32 = 1
    check(AudioUnitSetProperty(au, kAudioOutputUnitProperty_EnableIO,
                               kAudioUnitScope_Input, 1,
                               &one, UInt32(MemoryLayout<UInt32>.size)), "enable input")

    // Disable output on bus 0
    var zero: UInt32 = 0
    check(AudioUnitSetProperty(au, kAudioOutputUnitProperty_EnableIO,
                               kAudioUnitScope_Output, 0,
                               &zero, UInt32(MemoryLayout<UInt32>.size)), "disable output")

    // Set aggregate device as input
    var devID = device
    check(AudioUnitSetProperty(au, kAudioOutputUnitProperty_CurrentDevice,
                               kAudioUnitScope_Global, 0,
                               &devID, UInt32(MemoryLayout<AudioObjectID>.size)), "set device")

    // Output format: 16 kHz mono Int16
    var fmt = AudioStreamBasicDescription(
        mSampleRate: outputSampleRate,
        mFormatID: kAudioFormatLinearPCM,
        mFormatFlags: kAudioFormatFlagIsSignedInteger | kAudioFormatFlagIsPacked,
        mBytesPerPacket: 2 * outputChannels,
        mFramesPerPacket: 1,
        mBytesPerFrame: 2 * outputChannels,
        mChannelsPerFrame: outputChannels,
        mBitsPerChannel: 16,
        mReserved: 0
    )
    check(AudioUnitSetProperty(au, kAudioUnitProperty_StreamFormat,
                               kAudioUnitScope_Output, 1,
                               &fmt, UInt32(MemoryLayout<AudioStreamBasicDescription>.size)), "set format")

    // Render callback
    var cb = AURenderCallbackStruct(inputProc: renderCallback, inputProcRefCon: nil)
    check(AudioUnitSetProperty(au, kAudioOutputUnitProperty_SetInputCallback,
                               kAudioUnitScope_Global, 0,
                               &cb, UInt32(MemoryLayout<AURenderCallbackStruct>.size)), "set callback")

    check(AudioUnitInitialize(au), "init audio unit")
    return au
}

// ─── Render callback ────────────────────────────────────────────────────────
let renderCallback: AURenderCallback = {
    (_, ioActionFlags, inTimeStamp, _, inNumberFrames, _) -> OSStatus in

    let size = inNumberFrames * 2 * outputChannels
    var bufList = AudioBufferList(
        mNumberBuffers: 1,
        mBuffers: AudioBuffer(
            mNumberChannels: outputChannels,
            mDataByteSize: size,
            mData: malloc(Int(size))
        )
    )
    defer { free(bufList.mBuffers.mData) }

    guard let au = audioUnit else { return noErr }
    let status = AudioUnitRender(au, ioActionFlags, inTimeStamp, 1, inNumberFrames, &bufList)
    guard status == noErr else { return status }

    if let data = bufList.mBuffers.mData {
        if fwrite(data, 1, Int(bufList.mBuffers.mDataByteSize), stdout) == 0 {
            running = false
        }
        fflush(stdout)
    }
    return noErr
}

// ─── Main ───────────────────────────────────────────────────────────────────
if #available(macOS 14.2, *) {
    log("system-audio-tap: starting (16kHz mono Int16)")

    let (tap, agg) = createTapAndAggregate()
    tapID = tap
    aggregateID = agg

    let au = setupAudioUnit(device: agg)
    audioUnit = au

    check(AudioOutputUnitStart(au), "start capture")
    log("Capturing system audio → stdout. SIGINT/SIGTERM to stop.")

    while running { Thread.sleep(forTimeInterval: 0.1) }

    log("Shutting down...")
    AudioOutputUnitStop(au)
    AudioComponentInstanceDispose(au)
    AudioHardwareDestroyAggregateDevice(aggregateID)
    AudioHardwareDestroyProcessTap(tapID)
    log("Done.")
} else {
    log("Error: macOS 14.2+ required")
    exit(1)
}
