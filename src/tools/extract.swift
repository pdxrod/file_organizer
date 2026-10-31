// extract.swift — text extraction helper for file_organizer's router.
//
// macOS only. Uses system frameworks (no dependencies):
//   PDF     -> PDFKit text extraction (scanned PDFs fall back to page-1 OCR)
//   images  -> Vision OCR + scene classification (VNClassifyImageRequest)
//   video   -> AVFoundation frame grab + OCR + scene classification + metadata
//
// Build (done automatically by src/file_organizer/extractor.py):
//   swiftc -O extract.swift -o extract
//
// Usage: extract <file>
// Prints one JSON object on stdout:
//   {"text": "...", "meta": {"duration_seconds": "12", "title": "...",
//          "classifications": "crowd 0.82; flag 0.31; ..."}}
// Exit codes: 0 ok, 2 usage, 3 unreadable file.

import Foundation
import Vision
import PDFKit
import AVFoundation
import ImageIO

let IMAGE_EXTS: Set<String> = [
    "png", "jpg", "jpeg", "gif", "tif", "tiff", "bmp", "heic", "heif", "webp",
]
let VIDEO_EXTS: Set<String> = [
    "mp4", "m4v", "mov", "avi", "mkv", "webm", "flv", "wmv", "3gp", "3g2",
]

// ── Scene classification ─────────────────────────────────────────────
// VNClassifyImageRequest labels the whole image from a ~1300-term taxonomy
// ("crowd", "military_uniform", "flag", ...). We keep the top hits with
// confidence >= 0.2; the router looks for political scene words in them.

func classifyImage(_ cgImage: CGImage) -> [String] {
    let request = VNClassifyImageRequest()
    let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
    do { try handler.perform([request]) } catch { return [] }
    var labels: [String] = []
    for obs in request.results ?? [] {
        guard obs.confidence >= 0.2 else { continue }
        labels.append(String(format: "%@ %.2f", obs.identifier, obs.confidence))
        if labels.count >= 6 { break }
    }
    return labels
}

// ── OCR ──────────────────────────────────────────────────────────────

func ocr(_ cgImage: CGImage) -> String {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
    do { try handler.perform([request]) } catch { return "" }
    var lines: [String] = []
    for obs in request.results ?? [] {
        if let s = obs.topCandidates(1).first?.string {
            lines.append(s)
        }
    }
    return lines.joined(separator: "\n")
}

func ocrImageFile(_ url: URL) -> (String, [String]) {
    guard let src = CGImageSourceCreateWithURL(url as CFURL, nil),
          let img = CGImageSourceCreateImageAtIndex(src, 0, nil) else {
        return ("", [])
    }
    return (ocr(img), classifyImage(img))
}

// ── PDF ──────────────────────────────────────────────────────────────

func pdfText(_ url: URL) -> String {
    if let doc = PDFDocument(url: url), let text = doc.string, !text.isEmpty {
        return text
    }
    // Scanned PDF with no text layer: OCR the first page.
    let (ocrText, _) = ocrImageFile(url)
    return ocrText
}

// ── Video ────────────────────────────────────────────────────────────

func videoInfo(_ url: URL) -> (String, [String: String]) {
    let asset = AVURLAsset(url: url)
    var meta: [String: String] = [:]
    let seconds = CMTimeGetSeconds(asset.duration)
    if seconds.isFinite && seconds > 0 {
        meta["duration_seconds"] = String(Int(seconds))
    }
    for item in asset.commonMetadata {
        guard let key = item.commonKey?.rawValue,
              let value = item.stringValue, !value.isEmpty else { continue }
        meta[key] = value
    }

    var text = ""
    if seconds.isFinite && seconds > 0.5 {
        let generator = AVAssetImageGenerator(asset: asset)
        generator.appliesPreferredTrackTransform = true
        generator.requestedTimeToleranceBefore = .zero
        generator.requestedTimeToleranceAfter = .zero
        let at = CMTime(seconds: min(2.0, seconds * 0.15),
                        preferredTimescale: 600)
        var actual = CMTime.zero
        if let frame = try? generator.copyCGImage(at: at, actualTime: &actual) {
            text = ocr(frame)
            let labels = classifyImage(frame)
            if !labels.isEmpty {
                meta["classifications"] = labels.joined(separator: "; ")
            }
        }
    }
    return (text, meta)
}

// ── main ─────────────────────────────────────────────────────────────

let args = CommandLine.arguments
guard args.count > 1 else {
    print("usage: extract <file>")
    exit(2)
}
let url = URL(fileURLWithPath: args[1])
let ext = url.pathExtension.lowercased()

var text = ""
var meta: [String: String] = [:]

if ext == "pdf" {
    text = pdfText(url)
} else if IMAGE_EXTS.contains(ext) {
    var labels: [String] = []
    (text, labels) = ocrImageFile(url)
    if !labels.isEmpty {
        meta["classifications"] = labels.joined(separator: "; ")
    }
} else if VIDEO_EXTS.contains(ext) {
    (text, meta) = videoInfo(url)
} else {
    // Unsupported type: empty result (caller falls back to other means).
    text = ""
}

let output: [String: Any] = ["text": text, "meta": meta]
if let data = try? JSONSerialization.data(withJSONObject: output),
   let json = String(data: data, encoding: .utf8) {
    print(json)
} else {
    print("{\"text\": \"\", \"meta\": {}}")
}
exit(0)
