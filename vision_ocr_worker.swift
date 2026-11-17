import Foundation
import Vision
import ImageIO
import CoreGraphics

struct RegionTask: Codable {
    let name: String
    let language: String
    let level: String
    let x0: Double
    let y0: Double // top-origin normalized coordinate
    let x1: Double
    let y1: Double
}

struct WorkerInput: Codable {
    let imagePath: String
    let tasks: [RegionTask]
    let minimumTextHeight: Double?
}

struct Observation: Codable {
    let region: String
    let requestedLanguage: String
    let selectedLanguage: String
    let text: String
    let confidence: Float
    let x0: Double
    let y0: Double
    let x1: Double
    let y1: Double
}

struct WorkerOutput: Codable {
    let engine: String
    let imagePath: String
    let imageWidth: Int
    let imageHeight: Int
    let elapsedSeconds: Double
    let supportedLanguages: [String]
    let observations: [Observation]
}

func fail(_ message: String, code: Int32 = 1) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    exit(code)
}

func levelValue(_ value: String) -> VNRequestTextRecognitionLevel {
    return value.lowercased() == "fast" ? .fast : .accurate
}

func languages(for level: VNRequestTextRecognitionLevel) throws -> [String] {
    return try VNRecognizeTextRequest.supportedRecognitionLanguages(
        for: level,
        revision: VNRecognizeTextRequest.currentRevision
    )
}

func resolveLanguage(_ requested: String, available: [String]) -> String? {
    if available.contains(requested) { return requested }
    switch requested.lowercased() {
    case "zh", "zh-hant", "chinese_cht", "traditional-chinese":
        // Deliberately do not fall back to zh-Hans: this project requires Traditional Chinese.
        return available.first { $0.caseInsensitiveCompare("zh-Hant") == .orderedSame }
    case "pt", "portuguese", "pt-pt":
        return available.first { $0.caseInsensitiveCompare("pt-PT") == .orderedSame }
            ?? available.first { $0.caseInsensitiveCompare("pt-BR") == .orderedSame }
            ?? available.first { $0.lowercased().hasPrefix("pt-") }
    case "en", "english", "en-us":
        return available.first { $0.caseInsensitiveCompare("en-US") == .orderedSame }
            ?? available.first { $0.lowercased().hasPrefix("en-") }
    default:
        return nil
    }
}

let args = Array(CommandLine.arguments.dropFirst())
if args.count == 1 && args[0] == "--list-languages" {
    do {
        let values = try languages(for: .accurate).sorted()
        print(values.joined(separator: "\n"))
        exit(0)
    } catch {
        fail("Unable to query Vision languages: \(error)")
    }
}

let started = ProcessInfo.processInfo.systemUptime
do {
    let inputData = FileHandle.standardInput.readDataToEndOfFile()
    let input = try JSONDecoder().decode(WorkerInput.self, from: inputData)
    let imageURL = URL(fileURLWithPath: input.imagePath)
    guard let source = CGImageSourceCreateWithURL(imageURL as CFURL, nil),
          let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
        fail("Cannot load image: \(input.imagePath)")
    }

    let width = image.width
    let height = image.height
    var allObservations: [Observation] = []
    var languageCache: [String: [String]] = [:]

    for task in input.tasks {
        let level = levelValue(task.level)
        let levelKey = task.level.lowercased()
        let available: [String]
        if let cached = languageCache[levelKey] {
            available = cached
        } else {
            let fetched = try languages(for: level)
            languageCache[levelKey] = fetched
            available = fetched
        }

        guard let selected = resolveLanguage(task.language, available: available) else {
            throw NSError(domain: "MacauTBVision", code: 2, userInfo: [
                NSLocalizedDescriptionKey: "Requested language '\(task.language)' is unavailable for Vision \(task.level). Supported: \(available.joined(separator: ", "))"
            ])
        }

        let x0 = max(0.0, min(1.0, task.x0))
        let x1 = max(0.0, min(1.0, task.x1))
        let top0 = max(0.0, min(1.0, task.y0))
        let top1 = max(0.0, min(1.0, task.y1))
        guard x1 > x0, top1 > top0 else { continue }

        // Vision's normalized ROI origin is bottom-left; task coordinates are top-left.
        let roi = CGRect(x: x0, y: 1.0 - top1, width: x1 - x0, height: top1 - top0)
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = level
        request.recognitionLanguages = [selected]
        request.usesLanguageCorrection = false
        request.automaticallyDetectsLanguage = false
        request.minimumTextHeight = Float(input.minimumTextHeight ?? 0.004)
        request.regionOfInterest = roi

        let handler = VNImageRequestHandler(cgImage: image, options: [:])
        try handler.perform([request])

        for observation in request.results ?? [] {
            guard let candidate = observation.topCandidates(1).first else { continue }
            let rect = observation.boundingBox // normalized within the request's ROI
            // Convert the ROI-relative Vision box back to source-image pixel
            // coordinates. Vision uses a lower-left origin; Python uses top-left.
            let imageRect = VNImageRectForNormalizedRectUsingRegionOfInterest(
                rect, width, height, roi
            )
            allObservations.append(Observation(
                region: task.name,
                requestedLanguage: task.language,
                selectedLanguage: selected,
                text: candidate.string,
                confidence: candidate.confidence,
                x0: Double(imageRect.minX),
                y0: Double(CGFloat(height) - imageRect.maxY),
                x1: Double(imageRect.maxX),
                y1: Double(CGFloat(height) - imageRect.minY)
            ))
        }
    }

    let queriedLanguages = Array(Set(languageCache.values.flatMap { $0 })).sorted()
    let output = WorkerOutput(
        engine: "Apple Vision VNRecognizeTextRequest",
        imagePath: input.imagePath,
        imageWidth: width,
        imageHeight: height,
        elapsedSeconds: ProcessInfo.processInfo.systemUptime - started,
        supportedLanguages: queriedLanguages,
        observations: allObservations.sorted {
            if abs($0.y0 - $1.y0) > 2.0 { return $0.y0 < $1.y0 }
            return $0.x0 < $1.x0
        }
    )

    let encoder = JSONEncoder()
    encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
    let data = try encoder.encode(output)
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write("\n".data(using: .utf8)!)
} catch {
    fail("Vision OCR failed: \(error)")
}
