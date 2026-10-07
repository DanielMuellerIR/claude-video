// Apple-Vision-OCR: Konfidenz, normalisierte Box (Ursprung unten links), Text.
// Kompilierung und Cache verwaltet textframes.py; Aufruf: ocr <Bildpfad>.
import Vision
import AppKit
import Foundation

guard CommandLine.arguments.count == 2,
      let image = NSImage(contentsOfFile: CommandLine.arguments[1]),
      let tiff = image.tiffRepresentation,
      let bitmap = NSBitmapImageRep(data: tiff),
      let cgImage = bitmap.cgImage else {
    FileHandle.standardError.write(Data("Cannot read OCR image.\n".utf8))
    exit(1)
}

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.recognitionLanguages = ["de-DE", "en-US"]
request.usesLanguageCorrection = false

do {
    try VNImageRequestHandler(cgImage: cgImage, options: [:]).perform([request])
} catch {
    FileHandle.standardError.write(Data("Apple Vision text recognition failed.\n".utf8))
    exit(1)
}

for observation in request.results ?? [] {
    if let candidate = observation.topCandidates(1).first {
        let box = observation.boundingBox
        let text = candidate.string.replacingOccurrences(of: "\t", with: " ")
                                   .replacingOccurrences(of: "\n", with: " ")
                                   .replacingOccurrences(of: "\r", with: " ")
        print(String(format: "%.3f\t%.4f\t%.4f\t%.4f\t%.4f\t%@",
                     locale: Locale(identifier: "en_US_POSIX"),
                     candidate.confidence, box.minX, box.minY, box.width, box.height, text))
    }
}
