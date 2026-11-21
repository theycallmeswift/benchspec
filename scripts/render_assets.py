"""Render the brand assets under ``docs/assets`` from one source of truth.

Run with ``make assets``. It rewrites:

- ``benchspec-mark.svg`` and ``benchspec-mark-dark.svg``: the mark for light and
  dark grounds.
- ``benchspec-wordmark-light.svg`` and ``benchspec-wordmark-dark.svg``: the mark
  plus the name, outlined to paths.
- ``benchmark-terminal.png``: the terminal mock of ``benchspec run`` and its matrix.
- ``benchmark-terminal.gif``: the same mock, animated: the command is typed, the
  progress line lands, then the report is revealed one line at a time.
- ``social-preview.png``: the 1280x640 GitHub social card.

All text is set in the JetBrains Mono files under ``scripts/fonts``, so the output
is the same on every machine. The terminal colors mirror the ANSI bands
``benchspec.reporting.report.terminal_matrix`` uses (rates green ≥80%, yellow
≥50%, red below; deltas green up, red down, yellow zero).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

PROJECT_NAME = "benchspec"
ASSETS_DIR = Path(__file__).resolve().parent.parent / "docs" / "assets"
FONTS_DIR = Path(__file__).resolve().parent / "fonts"

NAVY = "#1B1F3B"
ORANGE = "#E85D2F"
SALMON = "#F2A48C"
GRAY = "#A5ACBE"
DARK_GROUND_GRAY = "#6E7891"

TERMINAL_TEXT = "#E8ECF1"
TERMINAL_MUTED = "#8B93A7"
TERMINAL_PROMPT = "#F0764B"
TERMINAL_GREEN = "#3FBF7F"
TERMINAL_YELLOW = "#E3BF5F"
TERMINAL_RED = "#E25E59"

# Shapes are drawn this many times larger, then downsampled, because Pillow's
# primitives are not anti-aliased. Text is anti-aliased natively and drawn at 1x.
SUPERSAMPLE = 4


class FontWeight(StrEnum):
    """The bundled JetBrains Mono weights, valued by their file name suffix."""

    REGULAR = "Regular"
    SEMIBOLD = "SemiBold"
    BOLD = "Bold"


def font_path(weight: FontWeight) -> Path:
    """Return the bundled font file for ``weight``.

    Raises:
        FileNotFoundError: if the file is missing from ``FONTS_DIR``.
    """
    path = FONTS_DIR / f"JetBrainsMono-{weight}.ttf"

    # Pillow searches the system font folders for a file it can't open.
    if not path.is_file():
        raise FileNotFoundError(f"bundled font missing: {path}")

    return path


def load_font(size: int, weight: FontWeight = FontWeight.REGULAR) -> ImageFont.FreeTypeFont:
    """Load the bundled font at ``size`` pixels.

    Raises:
        FileNotFoundError: if the font file is missing from ``FONTS_DIR``.
    """
    # Basic layout applies no ligatures, so `--count` renders as typed; Raqm,
    # where Pillow has it, would join the dashes.
    return ImageFont.truetype(font_path(weight), size, layout_engine=ImageFont.Layout.BASIC)


def supersampled_canvas(width: int, height: int, background: str) -> Image.Image:
    """Create a ``SUPERSAMPLE``x canvas to draw anti-aliased shapes on."""
    return Image.new("RGB", (width * SUPERSAMPLE, height * SUPERSAMPLE), background)


def downsample(canvas: Image.Image) -> Image.Image:
    """Shrink a supersampled canvas back to its target size."""
    width, height = canvas.size
    return canvas.resize((width // SUPERSAMPLE, height // SUPERSAMPLE), Image.Resampling.LANCZOS)


def draw_round_capped_polyline(
    draw: ImageDraw.ImageDraw, points: list[tuple[float, float]], *, color: str, stroke: float
) -> None:
    """Draw a polyline with round joins and round end caps, like an SVG stroke."""
    scaled = [(x * SUPERSAMPLE, y * SUPERSAMPLE) for x, y in points]
    scaled_stroke = stroke * SUPERSAMPLE
    radius = scaled_stroke / 2

    draw.line(scaled, fill=color, width=round(scaled_stroke), joint="curve")
    for end_x, end_y in (scaled[0], scaled[-1]):
        draw.ellipse((end_x - radius, end_y - radius, end_x + radius, end_y + radius), fill=color)


def draw_rounded_square(
    draw: ImageDraw.ImageDraw, x: float, y: float, *, size: float, radius: float, color: str
) -> None:
    """Draw one matrix cell at ``(x, y)`` in 1x coordinates."""
    box = tuple(value * SUPERSAMPLE for value in (x, y, x + size, y + size))
    draw.rounded_rectangle(box, radius=radius * SUPERSAMPLE, fill=color)


@dataclass(frozen=True)
class BracketMatrix:
    """Geometry of the bracket-matrix logo in 1x pixel coordinates.

    Attributes:
        column_xs: Left edge of each column of squares.
        row_ys: Top edge of each row of squares.
        square_size: Side length of one square.
        corner_radius: Corner radius of one square.
        bracket_stroke: Stroke width of the two brackets.
        left_bracket: Polyline of the left bracket, drawn with round caps.
        right_bracket: Polyline of the right bracket, drawn with round caps.
        square_colors: Fill per row, one color per column.
    """

    column_xs: list[float]
    row_ys: list[float]
    square_size: float
    corner_radius: float
    bracket_stroke: float
    left_bracket: list[tuple[float, float]]
    right_bracket: list[tuple[float, float]]
    square_colors: list[list[str]]

    def scaled(self, factor: float, *, origin: tuple[float, float]) -> BracketMatrix:
        """Return this geometry scaled by ``factor`` and translated to ``origin``."""
        origin_x, origin_y = origin

        def point(x: float, y: float) -> tuple[float, float]:
            """Scale and translate one point."""
            return origin_x + x * factor, origin_y + y * factor

        return BracketMatrix(
            column_xs=[origin_x + x * factor for x in self.column_xs],
            row_ys=[origin_y + y * factor for y in self.row_ys],
            square_size=self.square_size * factor,
            corner_radius=self.corner_radius * factor,
            bracket_stroke=self.bracket_stroke * factor,
            left_bracket=[point(x, y) for x, y in self.left_bracket],
            right_bracket=[point(x, y) for x, y in self.right_bracket],
            square_colors=self.square_colors,
        )


MARK_SIZE = 64
MARK = BracketMatrix(
    column_xs=[19, 35],
    row_ys=[14, 27, 40],
    square_size=10,
    corner_radius=2.5,
    bracket_stroke=4,
    left_bracket=[(14, 8), (6, 8), (6, 56), (14, 56)],
    right_bracket=[(50, 8), (58, 8), (58, 56), (50, 56)],
    square_colors=[[GRAY, ORANGE], [GRAY, SALMON], [GRAY, ORANGE]],
)


DARK_MARK = replace(
    MARK,
    square_colors=[
        [DARK_GROUND_GRAY, TERMINAL_PROMPT],
        [DARK_GROUND_GRAY, SALMON],
        [DARK_GROUND_GRAY, TERMINAL_PROMPT],
    ],
)


def draw_bracket_matrix(
    draw: ImageDraw.ImageDraw, geometry: BracketMatrix, *, bracket_color: str
) -> None:
    """Draw a bracket-matrix logo onto a supersampled canvas."""
    for bracket in (geometry.left_bracket, geometry.right_bracket):
        draw_round_capped_polyline(
            draw, bracket, color=bracket_color, stroke=geometry.bracket_stroke
        )

    for row_y, row_colors in zip(geometry.row_ys, geometry.square_colors, strict=True):
        for column_x, color in zip(geometry.column_xs, row_colors, strict=True):
            draw_rounded_square(
                draw,
                column_x,
                row_y,
                size=geometry.square_size,
                radius=geometry.corner_radius,
                color=color,
            )


WORDMARK_WIDTH = 250
WORDMARK_FONT_SIZE = 30
WORDMARK_LEFT = 72
WORDMARK_BASELINE = 43
WORDMARK_LETTER_SPACING_EM = -0.01


def svg_number(value: float) -> str:
    """Format a coordinate with at most two decimals."""
    return f"{round(value, 2):g}"


def wordmark_outline() -> str:
    """Return the SVG path data of the name, outlined from the bundled SemiBold.

    Raises:
        ValueError: if the font has no Unicode character map.
    """
    font = TTFont(font_path(FontWeight.SEMIBOLD))
    glyphs = font.getGlyphSet()
    glyph_names = font.getBestCmap()

    if glyph_names is None:
        raise ValueError("the bundled SemiBold has no Unicode character map")

    # fontTools sets table fields while parsing, where the type checker can't see them.
    units_per_em = int(vars(font["head"])["unitsPerEm"])
    scale = WORDMARK_FONT_SIZE / units_per_em
    pen = SVGPathPen(glyphs, ntos=svg_number)

    glyph_left = float(WORDMARK_LEFT)

    for character in PROJECT_NAME:
        glyph = glyphs[glyph_names[ord(character)]]
        glyph.draw(TransformPen(pen, (scale, 0, 0, -scale, glyph_left, WORDMARK_BASELINE)))
        glyph_left += (glyph.width + WORDMARK_LETTER_SPACING_EM * units_per_em) * scale

    return pen.getCommands()


def bracket_svg(bracket: list[tuple[float, float]], *, color: str, stroke: float) -> str:
    """Return one bracket as a round-capped SVG path."""
    start, *rest = (f"{svg_number(x)} {svg_number(y)}" for x, y in bracket)
    path_data = f"M{start} " + " ".join(f"L{point}" for point in rest)

    return (
        f'<path d="{path_data}" fill="none" stroke="{color}" '
        f'stroke-width="{svg_number(stroke)}" stroke-linecap="round"/>'
    )


def logo_svg(mark: BracketMatrix, *, ink: str, with_wordmark: bool) -> str:
    """Return a logo file: the mark, then the outlined name when ``with_wordmark``.

    Args:
        mark: Geometry and square colors of the 64x64 mark.
        ink: Fill of the brackets and the name.
        with_wordmark: Widen the canvas and add the name to the right of the mark.

    Returns:
        The SVG document.
    """
    width = WORDMARK_WIDTH if with_wordmark else MARK_SIZE
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{MARK_SIZE}" '
        f'viewBox="0 0 {width} {MARK_SIZE}" role="img" aria-label="{PROJECT_NAME}">',
        bracket_svg(mark.left_bracket, color=ink, stroke=mark.bracket_stroke),
        bracket_svg(mark.right_bracket, color=ink, stroke=mark.bracket_stroke),
    ]

    size, radius = svg_number(mark.square_size), svg_number(mark.corner_radius)

    for row_y, row_colors in zip(mark.row_ys, mark.square_colors, strict=True):
        for column_x, color in zip(mark.column_xs, row_colors, strict=True):
            lines.append(
                f'<rect x="{svg_number(column_x)}" y="{svg_number(row_y)}" '
                f'width="{size}" height="{size}" rx="{radius}" fill="{color}"/>'
            )

    if with_wordmark:
        lines.append(f'<path d="{wordmark_outline()}" fill="{ink}"/>')

    return "\n".join([*lines, "</svg>", ""])


def logo_svgs() -> dict[str, str]:
    """Return every logo file's content by file name."""
    return {
        "benchspec-mark.svg": logo_svg(MARK, ink=NAVY, with_wordmark=False),
        "benchspec-mark-dark.svg": logo_svg(DARK_MARK, ink=TERMINAL_TEXT, with_wordmark=False),
        "benchspec-wordmark-light.svg": logo_svg(MARK, ink=NAVY, with_wordmark=True),
        "benchspec-wordmark-dark.svg": logo_svg(DARK_MARK, ink=TERMINAL_TEXT, with_wordmark=True),
    }


SOCIAL_SIZE = (1280, 640)
SOCIAL_BACKGROUND = "#F3F4F8"
SOCIAL_TAGLINE_COLOR = "#4A5262"
SOCIAL_TAGLINE = ["Benchmark what your agent does,", "not what it says."]

# The right-hand graphic is a taller five-row take on the mark, not a scaled copy.
SOCIAL_GRAPHIC = BracketMatrix(
    column_xs=[880, 1000],
    row_ys=[90, 196, 302, 408, 514],
    square_size=84,
    corner_radius=21,
    bracket_stroke=12,
    left_bracket=[(869, 62), (844, 62), (844, 625), (869, 625)],
    right_bracket=[(1094, 62), (1120, 62), (1120, 625), (1094, 625)],
    square_colors=[[GRAY, ORANGE], [GRAY, SALMON], [GRAY, ORANGE], [GRAY, GRAY], [GRAY, ORANGE]],
)


def render_social_preview() -> Image.Image:
    """Render the 1280x640 social card: small lockup and tagline left, big graphic right."""
    canvas = supersampled_canvas(*SOCIAL_SIZE, SOCIAL_BACKGROUND)
    shapes = ImageDraw.Draw(canvas)
    draw_bracket_matrix(shapes, MARK.scaled(1.6, origin=(96, 235)), bracket_color=NAVY)
    draw_bracket_matrix(shapes, SOCIAL_GRAPHIC, bracket_color=NAVY)
    card = downsample(canvas)

    text = ImageDraw.Draw(card)
    wordmark_font = load_font(64, FontWeight.BOLD)
    wordmark_left_bearing = wordmark_font.getbbox(PROJECT_NAME)[0]
    text.text(
        (221 - wordmark_left_bearing, 245), PROJECT_NAME, font=wordmark_font, fill=NAVY, anchor="la"
    )

    tagline_font = load_font(30)
    for line_index, line in enumerate(SOCIAL_TAGLINE):
        left_bearing = tagline_font.getbbox(line)[0]
        text.text(
            (100 - left_bearing, 364 + 44 * line_index),
            line,
            font=tagline_font,
            fill=SOCIAL_TAGLINE_COLOR,
            anchor="la",
        )

    return card


TERMINAL_SIZE = (800, 330)
TERMINAL_BODY = "#14172E"
TERMINAL_TITLE_BAR = "#1F2440"
TERMINAL_TITLE_BAR_HEIGHT = 41
TERMINAL_CORNER_RADIUS = 10
TERMINAL_DOTS = [(24, TERMINAL_PROMPT), (44, SALMON), (64, GRAY)]
TERMINAL_DOT_RADIUS = 6.5

TERMINAL_FONT_SIZE = 15
TERMINAL_TEXT_LEFT = 25
TERMINAL_FIRST_LINE_TOP = 66
TERMINAL_LINE_PITCH = 24
TERMINAL_COLUMNS = 84
RULE_WIDTH = 80
# The eval label is left-aligned at column 0; each arm cell is right-aligned to end here.
MATRIX_COLUMN_ENDS = [49, 74]
# Extra space between the run's progress line and the report block.
REPORT_GAP = 7


@dataclass(frozen=True)
class Span:
    """A run of same-styled terminal text."""

    text: str
    color: str = TERMINAL_TEXT


@dataclass(frozen=True)
class TerminalLine:
    """One line of the terminal mock, with any extra vertical gap above it."""

    spans: list[Span] = field(default_factory=list)
    gap_before: int = 0


def rate_color(rate: int) -> str:
    """Color a pass rate by band, as the real terminal reporter does."""
    if rate >= 80:
        return TERMINAL_GREEN

    if rate >= 50:
        return TERMINAL_YELLOW

    return TERMINAL_RED


def delta_color(delta_pp: int) -> str:
    """Color a percentage-point delta by sign, as the real terminal reporter does."""
    if delta_pp > 0:
        return TERMINAL_GREEN

    if delta_pp < 0:
        return TERMINAL_RED

    return TERMINAL_YELLOW


def rate_cell(rate: int, delta_pp: int | None = None) -> list[Span]:
    """Render one matrix cell: a bare rate for the baseline arm, else ``rate (+Npp)``."""
    spans = [Span(f"{rate}%", color=rate_color(rate))]

    if delta_pp is not None:
        spans.append(Span(f" ({delta_pp:+d}pp)", color=delta_color(delta_pp)))

    return spans


def span_width(spans: list[Span]) -> int:
    """Return the width of ``spans`` in character columns."""
    return sum(len(span.text) for span in spans)


def matrix_line(label: Span, cells: list[list[Span]]) -> TerminalLine:
    """Lay out a matrix row: the label at column 0, each cell right-aligned to its column end."""
    spans = [label]

    for column_end, cell in zip(MATRIX_COLUMN_ENDS, cells, strict=True):
        padding = column_end - span_width(spans) - span_width(cell)
        spans.append(Span(" " * padding))
        spans.extend(cell)

    return TerminalLine(spans)


def prompt_line(command: str) -> TerminalLine:
    """Render the shell prompt with ``command`` typed so far."""
    return TerminalLine([Span("$ ", color=TERMINAL_PROMPT), Span(command)])


def progress_line(dots: int, *, complete: bool) -> TerminalLine:
    """Render pytest's per-file progress line, with the percentage once complete."""
    left = f"evals/hello/greets-by-name.eval.md {'.' * dots}"
    right = "[100%]" if complete else ""
    padding = TERMINAL_COLUMNS - 1 - len(left) - len(right)
    return TerminalLine([Span(f"{left}{' ' * padding}{right}", color=TERMINAL_MUTED)])


def report_lines() -> list[TerminalLine]:
    """Render the end-of-run report block, one entry per revealed line."""
    rule = f" {PROJECT_NAME} benchmark ".center(RULE_WIDTH, "=")
    header = matrix_line(Span("Eval"), [[Span("baseline")], [Span("trial")]])

    return [
        TerminalLine([Span(rule, color=TERMINAL_MUTED)], gap_before=REPORT_GAP),
        header,
        matrix_line(Span("hello/greets-by-name"), [rate_cell(50), rate_cell(100, 50)]),
        matrix_line(Span("hello-file/writes-greeting-file"), [rate_cell(0), rate_cell(100, 100)]),
        matrix_line(
            Span("hello-outside/allows-filesystem-traversal"), [rate_cell(0), rate_cell(100, 100)]
        ),
        TerminalLine([Span("-" * TERMINAL_COLUMNS, color=TERMINAL_MUTED)]),
        matrix_line(Span("All evals"), [rate_cell(17), rate_cell(100, 83)]),
        TerminalLine([Span("Report: tmp/evals/iteration_01/benchmark.md", color=TERMINAL_MUTED)]),
    ]


def render_terminal_chrome() -> Image.Image:
    """Render the empty terminal window: body, rounded title bar, traffic-light dots."""
    width, height = TERMINAL_SIZE
    canvas = supersampled_canvas(width, height, TERMINAL_BODY)
    draw = ImageDraw.Draw(canvas)

    title_bar_bottom = TERMINAL_TITLE_BAR_HEIGHT * SUPERSAMPLE
    corner_radius = TERMINAL_CORNER_RADIUS * SUPERSAMPLE
    # Round only the top corners: the rounded rectangle overshoots the bar, and the
    # plain rectangle squares off its bottom edge.
    draw.rounded_rectangle(
        (0, 0, width * SUPERSAMPLE, title_bar_bottom + corner_radius),
        radius=corner_radius,
        fill=TERMINAL_TITLE_BAR,
    )
    draw.rectangle(
        (0, title_bar_bottom, width * SUPERSAMPLE, height * SUPERSAMPLE), fill=TERMINAL_BODY
    )

    dot_y = TERMINAL_TITLE_BAR_HEIGHT / 2 * SUPERSAMPLE
    dot_radius = TERMINAL_DOT_RADIUS * SUPERSAMPLE
    for dot_x, color in TERMINAL_DOTS:
        center_x = dot_x * SUPERSAMPLE
        draw.ellipse(
            (center_x - dot_radius, dot_y - dot_radius, center_x + dot_radius, dot_y + dot_radius),
            fill=color,
        )

    return downsample(canvas)


def render_terminal_frame(chrome: Image.Image, lines: list[TerminalLine]) -> Image.Image:
    """Type ``lines`` onto a copy of the terminal chrome."""
    frame = chrome.copy()
    draw = ImageDraw.Draw(frame)
    font = load_font(TERMINAL_FONT_SIZE)

    line_top = TERMINAL_FIRST_LINE_TOP
    for line in lines:
        line_top += line.gap_before
        cursor_x = TERMINAL_TEXT_LEFT
        for span in line.spans:
            draw.text((cursor_x, line_top), span.text, font=font, fill=span.color, anchor="la")
            cursor_x += font.getlength(span.text)
        line_top += TERMINAL_LINE_PITCH

    return frame


TYPING_CHARS_PER_FRAME = 2
TYPING_FRAME_MS = 70
FIRST_PROGRESS_FRAME_MS = 500
SECOND_PROGRESS_FRAME_MS = 350
REPORT_LINE_FRAME_MS = 180
FINAL_HOLD_MS = 4000


def terminal_storyboard() -> list[tuple[list[TerminalLine], int]]:
    """Return the animation as ``(lines on screen, frame duration in ms)`` pairs.

    The command is typed a couple of characters per frame, the progress line lands in
    two beats, then the report is revealed one line at a time and held on the last frame.
    """
    command = f"{PROJECT_NAME} run -- --count 3"
    typed_lengths = [*range(0, len(command), TYPING_CHARS_PER_FRAME), len(command)]
    frames = [([prompt_line(command[:length])], TYPING_FRAME_MS) for length in typed_lengths]

    prompt = prompt_line(command)
    frames.append(([prompt, progress_line(3, complete=False)], FIRST_PROGRESS_FRAME_MS))
    progress = progress_line(6, complete=True)
    frames.append(([prompt, progress], SECOND_PROGRESS_FRAME_MS))

    report = report_lines()
    for revealed in range(1, len(report) + 1):
        duration = FINAL_HOLD_MS if revealed == len(report) else REPORT_LINE_FRAME_MS
        frames.append(([prompt, progress, *report[:revealed]], duration))

    return frames


def render_terminal_frames() -> tuple[list[Image.Image], list[int]]:
    """Render every animation frame and its duration."""
    chrome = render_terminal_chrome()
    storyboard = terminal_storyboard()
    frames = [render_terminal_frame(chrome, lines) for lines, _duration in storyboard]
    durations = [duration for _lines, duration in storyboard]

    return frames, durations


def write_gif(path: Path, frames: list[Image.Image], durations: list[int]) -> None:
    """Write ``frames`` as a looping GIF that shares one palette across every frame.

    The last frame shows every color the animation uses, so its palette covers the
    earlier frames too; sharing it keeps the file half the size of per-frame palettes.
    """
    palette = frames[-1].quantize(colors=256, method=Image.Quantize.MEDIANCUT)
    quantized = [frame.quantize(palette=palette, dither=Image.Dither.NONE) for frame in frames]
    quantized[0].save(
        path,
        save_all=True,
        append_images=quantized[1:],
        duration=durations,
        loop=0,
        optimize=False,
    )


def render_assets(assets_dir: Path) -> list[Path]:
    """Render every brand asset into ``assets_dir`` and return the files written."""
    written = []

    for name, svg in logo_svgs().items():
        (assets_dir / name).write_text(svg, encoding="utf-8", newline="\n")
        written.append(assets_dir / name)

    frames, durations = render_terminal_frames()
    frames[-1].save(assets_dir / "benchmark-terminal.png", optimize=True)
    write_gif(assets_dir / "benchmark-terminal.gif", frames, durations)
    render_social_preview().save(assets_dir / "social-preview.png", optimize=True)

    return [
        *written,
        assets_dir / "benchmark-terminal.png",
        assets_dir / "benchmark-terminal.gif",
        assets_dir / "social-preview.png",
    ]


def main() -> None:
    """Render every brand asset into ``ASSETS_DIR``."""
    for path in render_assets(ASSETS_DIR):
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
