import Foundation
import Vision
import ImageIO
import CoreGraphics
import CoreImage
import CoreImage.CIFilterBuiltins

struct RegionTask: Codable {
    let name: String
    let language: String
    let level: String
    let x0: Double
    let y0: Double // top-origin normalized coordinate
    let x1: Double
    let y1: Double
    // Optional per-region override; nil keeps Vision's default policy (off).
    let usesLanguageCorrection: Bool?
}

struct WorkerInput: Codable {
    let imagePath: String
    let tasks: [RegionTask]
    let minimumTextHeight: Double?
    // Opt-in experiment: contrast/grayscale preprocessing before recognition.
    let preprocessImage: Bool?
}

struct Observation: Codable {
    let region: String
    let requestedLanguage: String
    let selectedLanguage: String
    let requestedLevel: String
    let recognitionLevel: String
    let usesLanguageCorrection: Bool
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
    let levelsUsed: [String]
    let preprocessing: String
    let observations: [Observation]
}

// Reuse one CIContext: constructing it per page is expensive.
let sharedCIContext = CIContext(options: [.cacheIntermediates: false])

/// Optional contrast/grayscale preprocessing. This is an experiment, not a
/// quality guarantee: Vision already handles anti-aliased text, and boosting
/// contrast can erase thin strokes. It never changes image dimensions, so the
/// ROI coordinates stay valid. Falls back to the original image on failure.
func preprocessImage(_ cgImage: CGImage) -> CGImage {
    let ciImage = CIImage(cgImage: cgImage)
    let colorControls = CIFilter.colorControls()
    colorControls.inputImage = ciImage
    colorControls.contrast = 1.3
    colorControls.brightness = 0.05
    colorControls.saturation = 0.0
    guard let outputImage = colorControls.outputImage,
          let processedCGImage = sharedCIContext.createCGImage(
              outputImage,
              from: outputImage.extent,
              format: .RGBA8,
              colorSpace: CGColorSpaceCreateDeviceRGB()
          ) else {
        return cgImage
    }
    return processedCGImage
}

func fail(_ message: String, code: Int32 = 1) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    exit(code)
}

func levelValue(_ value: String) -> VNRequestTextRecognitionLevel {
    return value.lowercased() == "fast" ? .fast : .accurate
}

func levelName(_ level: VNRequestTextRecognitionLevel) -> String {
    return level == .fast ? "fast" : "accurate"
}

/// Traditional Chinese identifiers accepted by `resolveLanguage`.
func isTraditionalChinese(_ requested: String) -> Bool {
    switch requested.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
    case "zh", "zh-hant", "zh_hant", "zh-hk", "zh-mo", "zh-tw",
         "chinese_cht", "chinese-cht", "traditional-chinese":
        return true
    default:
        // Never treat zh-Hans or an unrelated identifier as Traditional Chinese.
        return false
    }
}

/// Vision `fast` has no Traditional Chinese recognizer. Rather than fail the
/// Chinese label/header regions (or silently drop them), route those tasks to
/// `accurate` and report the requested level separately from the level used.
func resolveTaskLevel(_ task: RegionTask) -> VNRequestTextRecognitionLevel {
    let requested = levelValue(task.level)
    if requested == .fast && isTraditionalChinese(task.language) {
        return .accurate
    }
    return requested
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
    case "zh", "zh-hant", "zh_hant", "zh-hk", "zh-mo", "zh-tw",
         "chinese_cht", "chinese-cht", "traditional-chinese":
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
if args.first == "--list-languages" {
    // Default stays "accurate" for backward compatibility with the Python
    // adapter and the documented setup check.
    let requested = args.count > 1 ? args[1].lowercased() : "accurate"
    guard requested == "fast" || requested == "accurate" else {
        fail("Usage: --list-languages [fast|accurate]")
    }
    do {
        let values = try languages(for: levelValue(requested)).sorted()
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
    // Preprocess once per page: ROI rectangles are relative, so all tasks can
    // share the same processed image and keep identical coordinates.
    let shouldPreprocess = input.preprocessImage ?? false
    let recognitionImage = shouldPreprocess ? preprocessImage(image) : image
    let preprocessingDescription = shouldPreprocess
        ? "grayscale(contrast=1.3,brightness=0.05)"
        : "none"
    var allObservations: [Observation] = []
    var languageCache: [String: [String]] = [:]
    var usedLevels: Set<String> = []

    for task in input.tasks {
        let level = resolveTaskLevel(task)
        let levelKey = levelName(level)
        usedLevels.insert(levelKey)
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
                NSLocalizedDescriptionKey: "Requested language '\(task.language)' is unavailable for Vision \(levelKey). Supported: \(available.joined(separator: ", "))"
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
        let useCorrection = task.usesLanguageCorrection ?? false
        request.usesLanguageCorrection = useCorrection
        request.automaticallyDetectsLanguage = false
        request.minimumTextHeight = Float(input.minimumTextHeight ?? 0.004)
        request.regionOfInterest = roi

        let handler = VNImageRequestHandler(cgImage: recognitionImage, options: [:])
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
                requestedLevel: levelName(levelValue(task.level)),
                recognitionLevel: levelKey,
                usesLanguageCorrection: useCorrection,
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
        levelsUsed: usedLevels.sorted(),
        preprocessing: preprocessingDescription,
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
