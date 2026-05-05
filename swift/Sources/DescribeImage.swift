import Foundation
import FoundationModels
import Vision
import AppKit

@available(macOS 26.0, *)
@Generable(description: "Filename slug for a screenshot")
struct FilenameSlug {
    @Guide(description: "Concise step-by-step reasoning for how you chose the slug.")
    var chain_of_thought: String

    // Regex-based guides currently trigger unsupportedGuide on runtime in this environment.
    @Guide(description: "3-8 lowercase words separated by hyphens, no punctuation.")
    var slug: String
}

private struct FilenameSlugTraceOutput: Encodable {
    let chain_of_thought: String
    let slug: String
}

@available(macOS 26.0, *)
struct DescribeImage {
    private struct Options {
        let imagePath: String?
        let ocrTextFilePath: String?
    }

    static let outputContractVersion = "v4-content-focus-fewshot"
    static let promptVersion = "content_focus_v1"
    private static let uiChromeWords: Set<String> = [
        "app", "application", "bar", "bookmark", "bookmarks", "browser", "chrome",
        "dock", "edit", "file", "help", "history", "menu", "new", "open",
        "profile", "profiles", "quit", "save", "tab", "toolbar", "view",
        "window",
    ]
    private static let contentKeywordStopWords: Set<String> = [
        "a", "an", "and", "are", "ask", "for", "from", "have", "heres", "i", "in",
        "is", "it", "its", "mar", "my", "of", "on", "or", "that", "the", "this",
        "to", "was", "we", "wed", "with", "you",
    ]

    static func main() async {
        guard let options = parseOptions(Array(CommandLine.arguments.dropFirst())) else {
            exit(1)
        }

        let imagePath = options.imagePath

        // Step 1: OCR via Vision framework (or optional fixture text file for eval mode)
        let ocrStartedAt = Date()
        var ocrText = ""
        var ocrObservationCount = 0
        var ocrEngine = "vision-recognize-text"

        if let ocrTextFilePath = options.ocrTextFilePath {
            do {
                ocrText = try String(contentsOfFile: ocrTextFilePath, encoding: .utf8)
                ocrEngine = "fixture-text-file"
            } catch {
                printError("Failed to read OCR text file: \(ocrTextFilePath): \(error)")
                exit(1)
            }
        } else {
            guard let imagePath else {
                printUsage()
                exit(1)
            }
            guard FileManager.default.fileExists(atPath: imagePath) else {
                printError("File not found: \(imagePath)")
                exit(1)
            }

            guard let nsImage = NSImage(contentsOfFile: imagePath),
                  let cgImage = nsImage.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
                printError("Failed to load image: \(imagePath)")
                exit(1)
            }

            do {
                let request = RecognizeTextRequest()
                let observations = try await request.perform(on: cgImage)
                ocrObservationCount = observations.count
                ocrText = observations
                    .compactMap { $0.topCandidates(1).first?.string }
                    .joined(separator: "\n")
            } catch {
                printError("OCR failed: \(error)")
            }
        }
        let ocrDurationMs = Int(Date().timeIntervalSince(ocrStartedAt) * 1000)

        if ocrText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            printError("No text extracted from image")
            exit(1)
        }

        // Step 2: Use FoundationModels to generate a semantic filename
        do {
            let instructions = """
                You generate short, descriptive filename slugs for screenshots.
                Think step by step using OCR evidence and put your reasoning in chain_of_thought.
                Prioritize the main content/topic/action over surrounding app or browser UI chrome.
                When content-specific terms exist, avoid generic UI words in slug such as browser, chrome, menu, tab, file, edit, view, window, help, bookmarks, and profiles.
                Return a slug with 3-8 lowercase words separated by hyphens.
                The slug must contain at least 3 words. If your first draft has fewer than 3 words, add specific OCR content words.
                If OCR is extremely sparse, use a 3-word fallback like screenshot-content-capture.
                No punctuation, no quotes, no extra commentary.
                """
            let session = LanguageModelSession(instructions: instructions)

            let promptLimitChars = 1500
            let truncated = String(ocrText.prefix(promptLimitChars))
            let ocrWasTruncated = ocrText.count > promptLimitChars
            let contentKeywords = extractContentKeywords(from: truncated)
            let contentKeywordsSection = contentKeywords.isEmpty
                ? "none"
                : contentKeywords.joined(separator: ", ")
            let sharedPromptContext = """
                OCR_TEXT_RAW:
                \(truncated)

                CONTENT_KEYWORDS:
                \(contentKeywordsSection)
                """

            let structuredPrompt = """
                TASK:
                Generate a content-focused filename slug for this screenshot.
                Fill BOTH schema fields:
                - chain_of_thought: one concise sentence using OCR evidence.
                - slug: 3-8 lowercase words, hyphen-separated.

                SCORING PRIORITY:
                1. Capture the main content/topic/action.
                2. Prefer specific nouns, entities, errors, or numbers from content.
                3. Ignore navigation/UI chrome unless no content topic is available.
                4. Always output 3-8 words in slug.

                STRUCTURED EXAMPLES:
                Example A INPUT:
                OCR_TEXT_RAW: ... can't send mail more than 500 miles ...
                CONTENT_KEYWORDS: trey, harris, send, mail, 500, miles, statistics
                Example A OUTPUT:
                chain_of_thought: The screenshot centers on an email delivery issue capped around 500 miles.
                slug: 500-miles-email-problem

                Example B INPUT:
                OCR_TEXT_RAW: Warning Session Ended ... exec: $SHELL: not found
                CONTENT_KEYWORDS: warning, session, ended, shell, not, found
                Example B OUTPUT:
                chain_of_thought: The screenshot shows a shell startup warning where the session ended unexpectedly.
                slug: session-ended-shell-error

                \(sharedPromptContext)
                """

            let fallbackPrompt = """
                TASK:
                Generate a content-focused filename slug for this screenshot.

                SCORING PRIORITY:
                1. Capture the main content/topic/action.
                2. Prefer specific nouns, entities, errors, or numbers from content.
                3. Ignore navigation/UI chrome unless no content topic is available.
                4. Always output 3-8 words in the slug.

                FEW_SHOT_EXAMPLES:
                Example A OCR_TEXT_RAW:
                Chrome File Edit View ... From: Trey Harris ... can't send mail more than 500 miles
                Example A CONTENT_KEYWORDS:
                trey, harris, send, mail, 500, miles, statistics, department
                Example A OUTPUT.slug:
                500-miles-email-problem

                Example B OCR_TEXT_RAW:
                Warning Session Ended ... bash: line 1: exec: $SHELL: not found
                Example B CONTENT_KEYWORDS:
                warning, session, ended, bash, shell, not, found
                Example B OUTPUT.slug:
                session-ended-shell-error

                \(sharedPromptContext)
                """

            let modelStartedAt = Date()
            var responseMode = "structured_generable"
            var rawOutput = ""
            var rawOutputUnparsed = ""
            var description = ""
            var structuredOutputJson: String? = nil
            var structuredSlug: String? = nil
            var structuredChainOfThought: String? = nil
            let structuredGenerationAttempted = true
            var structuredGenerationSucceeded = false
            var structuredGenerationError: String? = nil
            var structuredGenerationErrorType: String? = nil
            var structuredGenerationStrategy = "generable_description_guide"
            var structuredGenerationDurationMs: Int? = nil
            var fallbackGenerationDurationMs: Int? = nil

            do {
                let structuredStartedAt = Date()
                let options = GenerationOptions(
                    sampling: .greedy,
                    temperature: 0,
                    maximumResponseTokens: 96
                )
                let response = try await session.respond(
                    to: structuredPrompt,
                    generating: FilenameSlug.self,
                    includeSchemaInPrompt: true,
                    options: options
                )
                structuredSlug = response.content.slug
                    .trimmingCharacters(in: CharacterSet.whitespacesAndNewlines)
                    .lowercased()
                structuredChainOfThought = response.content.chain_of_thought
                    .trimmingCharacters(in: CharacterSet.whitespacesAndNewlines)
                let rawStructuredOutputJson = response.rawContent.jsonString
                rawOutputUnparsed = rawStructuredOutputJson
                rawOutput = rawOutputUnparsed
                if let structuredSlug, let structuredChainOfThought {
                    let traceOutput = FilenameSlugTraceOutput(
                        chain_of_thought: structuredChainOfThought,
                        slug: structuredSlug
                    )
                    if let encoded = try? JSONEncoder().encode(traceOutput),
                       let encodedString = String(data: encoded, encoding: .utf8) {
                        structuredOutputJson = encodedString
                    } else {
                        structuredOutputJson = rawStructuredOutputJson
                    }
                } else {
                    structuredOutputJson = rawStructuredOutputJson
                }
                structuredGenerationSucceeded = true
                description = extractSlugCandidate(from: structuredSlug ?? "") ?? ""
                structuredGenerationDurationMs = Int(Date().timeIntervalSince(structuredStartedAt) * 1000)
            } catch {
                responseMode = "legacy_text_fallback"
                structuredGenerationError = String(describing: error)
                structuredGenerationErrorType = String(describing: type(of: error))
                structuredGenerationStrategy = "legacy_text_fallback"
                let fallbackStartedAt = Date()
                let response = try await session.respond(to: fallbackPrompt)
                rawOutputUnparsed = response.content
                rawOutput = rawOutputUnparsed
                description = extractSlugCandidate(from: rawOutput) ?? ""
                fallbackGenerationDurationMs = Int(Date().timeIntervalSince(fallbackStartedAt) * 1000)
            }
            let modelDurationMs = Int(Date().timeIntervalSince(modelStartedAt) * 1000)

            if description.isEmpty {
                description = "screenshot"
            }
            let formatValid = isValidSlug(description)

            let keywords = description
                .lowercased()
                .components(separatedBy: "-")
                .filter { $0.count > 1 }

            var output: [String: Any] = [
                "chain_of_thought": structuredChainOfThought ?? "",
                "description": description,
                "keywords": keywords,
                "model": "apple-foundation-models",
                "prompt_version": promptVersion,
                "instructions": instructions,
                "prompt": structuredPrompt,
                "fallback_prompt": fallbackPrompt,
                "content_keywords": contentKeywords,
                "raw_output": rawOutput,
                "raw_output_unparsed": rawOutputUnparsed,
                "response_mode": responseMode,
                "output_contract_version": outputContractVersion,
                "format_valid": formatValid,
                "structured_generation_attempted": structuredGenerationAttempted,
                "structured_generation_succeeded": structuredGenerationSucceeded,
                "structured_generation_strategy": structuredGenerationStrategy,
                "ocr_text": truncated,
                "ocr": [
                    "engine": ocrEngine,
                    "observation_count": ocrObservationCount,
                    "full_text_chars": ocrText.count,
                    "prompt_text_chars": truncated.count,
                    "prompt_limit_chars": promptLimitChars,
                    "was_truncated": ocrWasTruncated,
                    "duration_ms": ocrDurationMs,
                ],
                "timing": [
                    "ocr_duration_ms": ocrDurationMs,
                    "model_duration_ms": modelDurationMs,
                ],
            ]
            if let structuredGenerationError {
                output["structured_generation_error"] = structuredGenerationError
            }
            if let structuredGenerationErrorType {
                output["structured_generation_error_type"] = structuredGenerationErrorType
            }
            if let structuredGenerationDurationMs {
                output["structured_generation_duration_ms"] = structuredGenerationDurationMs
            }
            if let fallbackGenerationDurationMs {
                output["fallback_generation_duration_ms"] = fallbackGenerationDurationMs
            }
            if let structuredSlug {
                output["structured_slug"] = structuredSlug
            }
            if let structuredChainOfThought {
                output["structured_chain_of_thought"] = structuredChainOfThought
            }
            if let structuredOutputJson {
                output["structured_output_json"] = structuredOutputJson
            }

            if let jsonData = try? JSONSerialization.data(withJSONObject: output, options: []),
               let jsonString = String(data: jsonData, encoding: .utf8) {
                print(jsonString)
            } else {
                let keywordsJson = keywords.map { "\"\($0)\"" }.joined(separator: ", ")
                print("{\"description\": \"\(description)\", \"keywords\": [\(keywordsJson)]}")
            }
        } catch {
            printError("FoundationModels error: \(error)")
            exit(1)
        }
    }

    static func printError(_ message: String) {
        FileHandle.standardError.write(Data((message + "\n").utf8))
    }

    static func printUsage() {
        printError("Usage: DescribeImage <image-path> [--ocr-text-file <path>]")
        printError("   or: DescribeImage --ocr-text-file <path>")
    }

    private static func parseOptions(_ args: [String]) -> Options? {
        if args.isEmpty {
            printUsage()
            return nil
        }

        var imagePath: String? = nil
        var ocrTextFilePath: String? = nil
        var index = 0
        while index < args.count {
            let value = args[index]
            if value == "--ocr-text-file" {
                let nextIndex = index + 1
                guard nextIndex < args.count else {
                    printError("Missing value for --ocr-text-file")
                    printUsage()
                    return nil
                }
                ocrTextFilePath = args[nextIndex]
                index += 2
                continue
            }
            if value.hasPrefix("--") {
                printError("Unknown option: \(value)")
                printUsage()
                return nil
            }
            if imagePath == nil {
                imagePath = value
                index += 1
                continue
            }
            printError("Unexpected argument: \(value)")
            printUsage()
            return nil
        }

        if imagePath == nil && ocrTextFilePath == nil {
            printUsage()
            return nil
        }

        return Options(imagePath: imagePath, ocrTextFilePath: ocrTextFilePath)
    }

    static func isValidSlug(_ value: String) -> Bool {
        value.wholeMatch(of: /[a-z0-9]+(?:-[a-z0-9]+){2,7}/) != nil
    }

    static func extractContentKeywords(from text: String, maxWords: Int = 16) -> [String] {
        var selected: [String] = []
        var seen: Set<String> = []

        for token in text.split(whereSeparator: \.isWhitespace) {
            let cleaned = token.lowercased().replacingOccurrences(
                of: "[^a-z0-9]",
                with: "",
                options: .regularExpression
            )
            if cleaned.count < 3 {
                continue
            }
            if cleaned.count > 20 {
                continue
            }
            if uiChromeWords.contains(cleaned) {
                continue
            }
            if contentKeywordStopWords.contains(cleaned) {
                continue
            }
            if cleaned.contains("http")
                || cleaned.contains("www")
                || cleaned.contains("com")
                || cleaned.contains("org")
                || cleaned.contains("edu") {
                continue
            }
            if seen.contains(cleaned) {
                continue
            }
            seen.insert(cleaned)
            selected.append(cleaned)
            if selected.count >= maxWords {
                break
            }
        }

        return selected
    }

    static func normalizeSlug(_ value: String) -> String {
        let lowered = value.lowercased()
        let replaced = lowered.replacingOccurrences(
            of: "[^a-z0-9]+",
            with: "-",
            options: .regularExpression
        )
        let compact = replaced.replacingOccurrences(
            of: "-{2,}",
            with: "-",
            options: .regularExpression
        )
        let trimmed = compact.trimmingCharacters(in: CharacterSet(charactersIn: "-"))
        if trimmed.isEmpty {
            return ""
        }
        let words = trimmed.split(separator: "-")
        return words.prefix(8).joined(separator: "-")
    }

    static func extractSlugCandidate(from value: String) -> String? {
        let lowered = value.lowercased()
        let pattern = #"[a-z0-9]+(?:-[a-z0-9]+){2,7}"#
        if let regex = try? NSRegularExpression(pattern: pattern) {
            let range = NSRange(lowered.startIndex..<lowered.endIndex, in: lowered)
            let matches = regex.matches(in: lowered, options: [], range: range)
            if !matches.isEmpty {
                let candidates = matches.compactMap { match -> String? in
                    guard let r = Range(match.range, in: lowered) else {
                        return nil
                    }
                    return String(lowered[r])
                }
                if let longest = candidates.max(by: { $0.count < $1.count }) {
                    return longest
                }
            }
        }

        let normalized = normalizeSlug(lowered)
        return normalized.isEmpty ? nil : normalized
    }
}

if #available(macOS 26.0, *) {
    await DescribeImage.main()
} else {
    FileHandle.standardError.write(Data("Requires macOS 26.0 or later\n".utf8))
    exit(1)
}
