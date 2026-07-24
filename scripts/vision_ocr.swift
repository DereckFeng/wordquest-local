#!/usr/bin/env swift

import Foundation
import ImageIO
import Vision

struct OCRLine: Codable {
    let text: String
    let confidence: Float
    let x: Double
    let y: Double
    let width: Double
    let height: Double
}

struct OCRPage: Codable {
    let path: String
    let orientation: String
    let score: Double
    let lines: [OCRLine]
}

func recognize(url: URL, orientation: CGImagePropertyOrientation) throws -> (Double, [OCRLine]) {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["en-US"]
    request.usesLanguageCorrection = true
    request.minimumTextHeight = 0.005

    let handler = VNImageRequestHandler(url: url, orientation: orientation, options: [:])
    try handler.perform([request])
    let observations = request.results ?? []
    let lines = observations.compactMap { observation -> OCRLine? in
        guard let candidate = observation.topCandidates(1).first else { return nil }
        let box = observation.boundingBox
        return OCRLine(
            text: candidate.string,
            confidence: candidate.confidence,
            x: box.origin.x,
            y: box.origin.y,
            width: box.width,
            height: box.height
        )
    }.sorted {
        if abs($0.y - $1.y) > 0.01 { return $0.y > $1.y }
        return $0.x < $1.x
    }
    let score = lines.reduce(0.0) { partial, line in
        partial + Double(line.confidence) * Double(max(1, line.text.count))
    }
    return (score, lines)
}

let encoder = JSONEncoder()
encoder.outputFormatting = [.withoutEscapingSlashes]

for argument in CommandLine.arguments.dropFirst() {
    let url = URL(fileURLWithPath: argument)
    do {
        let upright = try recognize(url: url, orientation: .up)
        let inverted = try recognize(url: url, orientation: .down)
        let selected = upright.0 >= inverted.0 ? upright : inverted
        let orientation = upright.0 >= inverted.0 ? "up" : "down"
        let page = OCRPage(path: argument, orientation: orientation, score: selected.0, lines: selected.1)
        let data = try encoder.encode(page)
        print(String(decoding: data, as: UTF8.self))
        fflush(stdout)
    } catch {
        let message = ["path": argument, "error": String(describing: error)]
        let data = try JSONSerialization.data(withJSONObject: message)
        print(String(decoding: data, as: UTF8.self))
        fflush(stdout)
    }
}
