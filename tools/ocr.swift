// 用 macOS 自带的 Vision 框架做中文文字识别
// 用法: swift ocr.swift <图片路径>
// 输出: JSON 数组 [{text, x, y, w, h}]（坐标已归一化到 0~1，原点在左下）

import Foundation
import Vision
import AppKit

guard CommandLine.arguments.count > 1 else {
    FileHandle.standardError.write("用法: swift ocr.swift <图片路径>\n".data(using: .utf8)!)
    exit(2)
}

let path = CommandLine.arguments[1]
guard let image = NSImage(contentsOfFile: path),
      let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    FileHandle.standardError.write("读不了这张图: \(path)\n".data(using: .utf8)!)
    exit(1)
}

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.recognitionLanguages = ["zh-Hans", "en-US"]
request.usesLanguageCorrection = true
// 第 2 个参数之后全部当成「自定义词表」，用来提示课名 / 教室 / 院系这类专有词，能提高命中率
if CommandLine.arguments.count > 2 {
    request.customWords = Array(CommandLine.arguments.dropFirst(2))
}

let handler = VNImageRequestHandler(cgImage: cg, options: [:])
do {
    try handler.perform([request])
} catch {
    FileHandle.standardError.write("识别失败: \(error)\n".data(using: .utf8)!)
    exit(1)
}

var results: [[String: Any]] = []
for observation in request.results ?? [] {
    guard let candidate = observation.topCandidates(1).first else { continue }
    let box = observation.boundingBox
    results.append([
        "text": candidate.string,
        "x": Double(box.minX),
        "y": Double(box.minY),
        "w": Double(box.width),
        "h": Double(box.height),
        "conf": Double(candidate.confidence),
    ])
}

// 按从上到下、从左到右排序（Vision 的原点在左下，所以 y 大的在上面）
results.sort { a, b in
    let ay = a["y"] as! Double, by = b["y"] as! Double
    if abs(ay - by) > 0.01 { return ay > by }
    return (a["x"] as! Double) < (b["x"] as! Double)
}

let data = try JSONSerialization.data(withJSONObject: results, options: [])
print(String(data: data, encoding: .utf8)!)
