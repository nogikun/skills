package main

import (
	"errors"
	"flag"
	"fmt"
	"io"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"unicode/utf8"
)

const (
	exitOK                  = 0
	exitLint                = 1
	exitSystem              = 2
	recommendedClaatInstall = "go install github.com/googlecodelabs/tools/claat@v0.0.0-20240220115335-873fe39d02dc"
)

var (
	idPattern        = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_-]*$`)
	durationPattern  = regexp.MustCompile(`^Duration:\s*(\d+):(\d{1,2}):(\d{2})$`)
	srcAttribute     = regexp.MustCompile(`src="[^"]*\\[^"]*"`)
	backslashRun     = regexp.MustCompile(`\\+`)
	requiredMetadata = []string{
		"summary",
		"id",
		"categories",
		"environments",
		"status",
		"feedback link",
		"analytics account",
	}
	metadataKeys = map[string]bool{
		"summary":           true,
		"id":                true,
		"categories":        true,
		"environments":      true,
		"status":            true,
		"feedback link":     true,
		"analytics account": true,
	}
)

type diagnostic struct {
	path    string
	line    int
	column  int
	code    string
	message string
}

func (d diagnostic) String() string {
	return fmt.Sprintf("%s:%d:%d: error %s %s", d.path, d.line, d.column, d.code, d.message)
}

type metadataValue struct {
	value string
	line  int
}

type stepState struct {
	title           string
	line            int
	durationChecked bool
	durationFound   bool
	hasBody         bool
}

var findClaat = exec.LookPath
var runClaat = executeClaat

func main() {
	os.Exit(run(os.Args[1:], os.Stdout, os.Stderr))
}

func run(args []string, stdout, stderr io.Writer) int {
	if len(args) == 0 {
		usage(stderr)
		return exitSystem
	}

	switch args[0] {
	case "help", "-h", "--help":
		usage(stdout)
		return exitOK
	case "lint":
		return runLint(args[1:], stdout, stderr)
	case "build":
		return runBuild(args[1:], stdout, stderr)
	default:
		fmt.Fprintf(stderr, "claat-tools: unknown command %q\n", args[0])
		usage(stderr)
		return exitSystem
	}
}

func usage(w io.Writer) {
	fmt.Fprintln(w, "Usage:")
	fmt.Fprintln(w, "  claat-tools lint MANUAL.md")
	fmt.Fprintln(w, "  claat-tools build [-output DIR] MANUAL.md")
}

func runLint(args []string, stdout, stderr io.Writer) int {
	fs := flag.NewFlagSet("lint", flag.ContinueOnError)
	fs.SetOutput(stderr)
	fs.Usage = func() {
		fmt.Fprintln(stderr, "Usage: claat-tools lint MANUAL.md")
	}
	if err := fs.Parse(args); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return exitOK
		}
		return exitSystem
	}
	if fs.NArg() != 1 {
		fs.Usage()
		return exitSystem
	}

	diagnostics, err := lintFile(fs.Arg(0))
	if err != nil {
		fmt.Fprintf(stderr, "claat-tools: %s\n", err)
		return exitSystem
	}
	printDiagnostics(stderr, diagnostics)
	if len(diagnostics) > 0 {
		return exitLint
	}
	return exitOK
}

func runBuild(args []string, stdout, stderr io.Writer) int {
	fs := flag.NewFlagSet("build", flag.ContinueOnError)
	fs.SetOutput(stderr)
	fs.Usage = func() {
		fmt.Fprintln(stderr, "Usage: claat-tools build [-output DIR] MANUAL.md")
	}
	output := fs.String("output", "output", "directory for generated HTML")
	fs.StringVar(output, "o", "output", "directory for generated HTML")
	if err := fs.Parse(args); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return exitOK
		}
		return exitSystem
	}
	if fs.NArg() != 1 {
		fs.Usage()
		return exitSystem
	}

	input := fs.Arg(0)
	diagnostics, err := lintFile(input)
	if err != nil {
		fmt.Fprintf(stderr, "claat-tools: %s\n", err)
		return exitSystem
	}
	printDiagnostics(stderr, diagnostics)
	if len(diagnostics) > 0 {
		return exitLint
	}
	if strings.TrimSpace(*output) == "" {
		fmt.Fprintln(stderr, "claat-tools: output directory must not be empty")
		return exitSystem
	}

	claat, err := findClaat("claat")
	if err != nil {
		fmt.Fprintln(stderr, "claat-tools: claat was not found in PATH")
		fmt.Fprintf(stderr, "install it with: %s\n", recommendedClaatInstall)
		return exitSystem
	}
	if err := runClaat(claat, input, *output, stdout, stderr); err != nil {
		var exitErr *exec.ExitError
		if errors.As(err, &exitErr) {
			return exitErr.ExitCode()
		}
		fmt.Fprintf(stderr, "claat-tools: failed to run claat: %s\n", err)
		return exitSystem
	}
	if err := normalizeAssetPaths(*output); err != nil {
		fmt.Fprintf(stderr, "claat-tools: failed to normalize asset paths: %s\n", err)
		return exitSystem
	}
	return exitOK
}

// claat が埋め込む配布元 storage.googleapis.com/claat-public は 403 を返すようになり、
// スタイルも custom element も読み込めない（見出しだけが並んだ素の HTML になる）。
// claat 本体はアーカイブ済みで直らないため、生きている配布元へ差し替える。
// codelab-elements は Google が npm に出したものと同じ中身で、残り 2 つは
// ES5 実装の custom element を今のブラウザで動かすために要る。
var claatAssets = strings.NewReplacer(
	"https://storage.googleapis.com/claat-public/codelab-elements.css",
	"https://cdn.jsdelivr.net/npm/codelab-elements@1.0.1/codelab-elements.css",
	"https://storage.googleapis.com/claat-public/codelab-elements.js",
	"https://cdn.jsdelivr.net/npm/codelab-elements@1.0.1/codelab-elements.js",
	"https://storage.googleapis.com/claat-public/native-shim.js",
	"https://cdn.jsdelivr.net/npm/@webcomponents/webcomponentsjs@2.8.0/custom-elements-es5-adapter.js",
	"https://storage.googleapis.com/claat-public/custom-elements.min.js",
	"https://cdn.jsdelivr.net/npm/@webcomponents/custom-elements@1.6.0/custom-elements.min.js",
	"https://storage.googleapis.com/claat-public/prettify.js",
	"https://cdn.jsdelivr.net/npm/code-prettify@0.1.0/loader/prettify.js",
)

// claat（アーカイブ済み）は img の src を OS のパス区切りで書くため、Windows では
// src="img\\x.png" になる。ブラウザは URL の \ を / として解釈するので img//x.png を
// 取りに行き、画像が表示されない。生成後に src 属性の \ だけを / へ直す。
func normalizeAssetPaths(output string) error {
	return filepath.WalkDir(output, func(path string, entry fs.DirEntry, err error) error {
		if errors.Is(err, fs.ErrNotExist) {
			// claat が何も書かなかった場合は直すものが無い。ここで失敗させない。
			return nil
		}
		if err != nil || entry.IsDir() || !strings.HasSuffix(strings.ToLower(path), ".html") {
			return err
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		fixed := srcAttribute.ReplaceAllStringFunc(string(data), func(match string) string {
			// claat は区切りを 2 つ重ねて書くので、連続した \ は 1 つの / に畳む。
			return backslashRun.ReplaceAllString(match, "/")
		})
		fixed = claatAssets.Replace(fixed)
		if fixed == string(data) {
			return nil
		}
		return os.WriteFile(path, []byte(fixed), 0o644)
	})
}

func executeClaat(path, input, output string, stdout, stderr io.Writer) error {
	command := exec.Command(path, "export", "-o", output, input)
	command.Stdout = stdout
	command.Stderr = stderr
	return command.Run()
}

func printDiagnostics(w io.Writer, diagnostics []diagnostic) {
	for _, diagnostic := range diagnostics {
		fmt.Fprintln(w, diagnostic)
	}
}

func lintFile(path string) ([]diagnostic, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("cannot read %s: %w", path, err)
	}
	if !utf8.Valid(data) {
		return nil, fmt.Errorf("%s is not valid UTF-8", path)
	}
	return lintMarkdown(path, string(data)), nil
}

func lintMarkdown(path, text string) []diagnostic {
	lines := strings.Split(text, "\n")
	diagnostics := make([]diagnostic, 0)
	metadata := make(map[string]metadataValue)
	var current *stepState
	titleSeen := false
	titleLine := 1
	stepCount := 0
	var fence byte
	fenceLength := 0
	fenceLine := 0

	for index, rawLine := range lines {
		lineNumber := index + 1
		line := strings.TrimSuffix(rawLine, "\r")

		if fence != 0 {
			trimmed := strings.TrimSpace(line)
			if marker, length, ok := fenceInfo(line); ok && marker == fence && length >= fenceLength && strings.TrimSpace(trimmed[length:]) == "" {
				fence = 0
			}
			continue
		}
		if marker, length, ok := fenceInfo(line); ok {
			fence = marker
			fenceLength = length
			fenceLine = lineNumber
			continue
		}

		if level, heading, ok := headingInfo(line); ok {
			switch level {
			case 1:
				if titleSeen || stepCount > 0 {
					diagnostics = append(diagnostics, diagnostic{path, lineNumber, 1, "DOC001", "only one page title is allowed"})
				} else {
					titleSeen = true
					titleLine = lineNumber
				}
				continue
			case 2:
				if !titleSeen {
					diagnostics = append(diagnostics, diagnostic{path, lineNumber, 1, "STEP001", "a step must appear after the page title"})
					continue
				}
				finishStep(path, &diagnostics, current)
				current = &stepState{title: heading, line: lineNumber}
				stepCount++
				continue
			case 3, 4:
				if !titleSeen || current == nil {
					diagnostics = append(diagnostics, diagnostic{path, lineNumber, 1, "STEP001", "a subheading must appear inside a step"})
					continue
				}
			case 5, 6:
				diagnostics = append(diagnostics, diagnostic{path, lineNumber, 1, "STEP001", "heading levels 5 and 6 are not supported"})
				continue
			}
		}

		if !titleSeen {
			if strings.TrimSpace(line) == "" {
				continue
			}
			key, value, ok := metadataLine(line)
			if !ok {
				diagnostics = append(diagnostics, diagnostic{path, lineNumber, 1, "META003", "expected metadata in `key: value` form before the page title"})
				continue
			}
			if !metadataKeys[key] {
				diagnostics = append(diagnostics, diagnostic{path, lineNumber, 1, "META002", fmt.Sprintf("unknown metadata key %q", key)})
				continue
			}
			if _, exists := metadata[key]; exists {
				diagnostics = append(diagnostics, diagnostic{path, lineNumber, 1, "META002", fmt.Sprintf("duplicate metadata key %q", key)})
				continue
			}
			metadata[key] = metadataValue{value: value, line: lineNumber}
			continue
		}

		if current == nil {
			continue
		}
		trimmed := strings.TrimSpace(line)
		if !current.durationChecked {
			if trimmed == "" {
				continue
			}
			current.durationChecked = true
			if parseDuration(trimmed) {
				current.durationFound = true
				continue
			}
			diagnostics = append(diagnostics, diagnostic{path, lineNumber, 1, "STEP002", "expected `Duration: H:M:SS` immediately after the step heading"})
			current.hasBody = true
			continue
		}
		if trimmed != "" {
			current.hasBody = true
		}
		checkImages(path, line, lineNumber, &diagnostics)
	}

	finishStep(path, &diagnostics, current)
	if fence != 0 {
		diagnostics = append(diagnostics, diagnostic{path, fenceLine, 1, "MD001", "code fence is not closed"})
	}
	if !titleSeen {
		diagnostics = append(diagnostics, diagnostic{path, 1, 1, "DOC001", "page title is missing"})
	}
	if stepCount == 0 {
		diagnostics = append(diagnostics, diagnostic{path, titleLine, 1, "STEP001", "at least one `##` step is required"})
	}
	validateMetadata(path, metadata, &diagnostics)
	return diagnostics
}

func finishStep(path string, diagnostics *[]diagnostic, step *stepState) {
	if step == nil {
		return
	}
	if !step.durationChecked {
		*diagnostics = append(*diagnostics, diagnostic{path, step.line, 1, "STEP002", "expected `Duration: H:M:SS` immediately after the step heading"})
	}
	if !step.hasBody {
		*diagnostics = append(*diagnostics, diagnostic{path, step.line, 1, "STEP003", fmt.Sprintf("step %q has no body", step.title)})
	}
}

func validateMetadata(path string, metadata map[string]metadataValue, diagnostics *[]diagnostic) {
	for _, key := range requiredMetadata {
		if _, ok := metadata[key]; !ok {
			*diagnostics = append(*diagnostics, diagnostic{path, 1, 1, "META001", fmt.Sprintf("required metadata %q is missing", key)})
		}
	}
	for key, item := range metadata {
		if strings.TrimSpace(item.value) == "" {
			*diagnostics = append(*diagnostics, diagnostic{path, item.line, 1, "META003", fmt.Sprintf("metadata %q must not be empty", key)})
			continue
		}
		switch key {
		case "id":
			if !idPattern.MatchString(item.value) {
				*diagnostics = append(*diagnostics, diagnostic{path, item.line, 1, "META003", "id must match `[A-Za-z0-9][A-Za-z0-9_-]*`"})
			}
		case "categories":
			if !validList(item.value, nil) {
				*diagnostics = append(*diagnostics, diagnostic{path, item.line, 1, "META003", "categories must be a non-empty comma-separated list"})
			}
		case "environments":
			if !validList(item.value, map[string]bool{"Web": true, "Kiosk": true}) {
				*diagnostics = append(*diagnostics, diagnostic{path, item.line, 1, "META003", "environments must contain only Web or Kiosk"})
			}
		case "status":
			switch item.value {
			case "Draft", "Published", "Deprecated", "Hidden":
			default:
				*diagnostics = append(*diagnostics, diagnostic{path, item.line, 1, "META003", "status must be Draft, Published, Deprecated, or Hidden"})
			}
		}
	}
}

func validList(value string, allowed map[string]bool) bool {
	parts := strings.Split(value, ",")
	if len(parts) == 0 {
		return false
	}
	for _, part := range parts {
		part = strings.TrimSpace(part)
		if part == "" || (allowed != nil && !allowed[part]) {
			return false
		}
	}
	return true
}

func metadataLine(line string) (key, value string, ok bool) {
	separator := strings.IndexByte(line, ':')
	if separator <= 0 {
		return "", "", false
	}
	key = strings.TrimSpace(line[:separator])
	if key == "" {
		return "", "", false
	}
	return key, strings.TrimSpace(line[separator+1:]), true
}

func headingInfo(line string) (level int, title string, ok bool) {
	trimmed := strings.TrimLeft(line, " \t")
	level = 0
	for level < len(trimmed) && trimmed[level] == '#' {
		level++
	}
	if level == 0 || level > 6 || level == len(trimmed) || (trimmed[level] != ' ' && trimmed[level] != '\t') {
		return 0, "", false
	}
	title = strings.TrimSpace(trimmed[level:])
	if title == "" {
		return 0, "", false
	}
	return level, title, true
}

func fenceInfo(line string) (marker byte, length int, ok bool) {
	trimmed := strings.TrimSpace(line)
	if len(trimmed) < 3 || (trimmed[0] != '`' && trimmed[0] != '~') {
		return 0, 0, false
	}
	marker = trimmed[0]
	for length < len(trimmed) && trimmed[length] == marker {
		length++
	}
	return marker, length, length >= 3
}

func parseDuration(line string) bool {
	matches := durationPattern.FindStringSubmatch(strings.TrimSpace(line))
	if matches == nil {
		return false
	}
	minutes, errMinutes := strconv.Atoi(matches[2])
	seconds, errSeconds := strconv.Atoi(matches[3])
	return errMinutes == nil && errSeconds == nil && minutes <= 59 && seconds <= 59
}

func checkImages(path, line string, lineNumber int, diagnostics *[]diagnostic) {
	for offset := 0; offset < len(line); {
		start := strings.Index(line[offset:], "![")
		if start < 0 {
			return
		}
		start += offset
		labelEnd := strings.Index(line[start+2:], "](")
		if labelEnd < 0 {
			return
		}
		open := start + 2 + labelEnd + 1
		close := strings.IndexByte(line[open+1:], ')')
		if close < 0 {
			return
		}
		destination := strings.TrimSpace(line[open+1 : open+1+close])
		destination = imageDestination(destination)
		if destination != "" && isLocalPath(destination) {
			imagePath := filepath.Join(filepath.Dir(path), filepath.FromSlash(destination))
			info, err := os.Stat(imagePath)
			if err != nil || info.IsDir() {
				*diagnostics = append(*diagnostics, diagnostic{path, lineNumber, start + 1, "ASSET001", fmt.Sprintf("local image not found: %s", destination)})
			}
		}
		offset = open + 1 + close
	}
}

func imageDestination(value string) string {
	if strings.HasPrefix(value, "<") {
		if end := strings.IndexByte(value, '>'); end >= 0 {
			return strings.TrimSpace(value[1:end])
		}
	}
	fields := strings.Fields(value)
	if len(fields) == 0 {
		return ""
	}
	return fields[0]
}

func isLocalPath(value string) bool {
	lower := strings.ToLower(value)
	return !strings.HasPrefix(lower, "http://") &&
		!strings.HasPrefix(lower, "https://") &&
		!strings.HasPrefix(lower, "data:") &&
		!strings.HasPrefix(value, "//") &&
		!filepath.IsAbs(value)
}
