# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow>=11,<13"]
# ///
"""Render the raster brand assets under ``docs/assets`` from one source of truth.

Run with ``uv run scripts/render_assets.py`` (or ``make assets``). It rewrites:

- ``benchmark-terminal.png`` — the terminal mock of ``benchspec run`` and its matrix.
- ``benchmark-terminal.gif`` — the same mock, animated: the command is typed, the
  progress line lands, then the report is revealed one line at a time.
- ``social-preview.png`` — the 1280x640 GitHub social card.

The mark geometry mirrors ``benchspec-mark.svg``; the terminal colors mirror the ANSI
bands ``benchspec.reporting.report.terminal_matrix`` uses (rates green ≥80%, yellow
≥50%, red below; deltas green up, red down, yellow zero).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PROJECT_NAME = "benchspec"
ASSETS_DIR = Path(__file__).resolve().parent.parent / "docs" / "assets"

NAVY = "#1B1F3B"
ORANGE = "#E85D2F"
SALMON = "#F2A48C"
GRAY = "#A5ACBE"

# Shapes are drawn this many times larger, then downsampled, because Pillow's
# primitives are not anti-aliased. Text is anti-aliased natively and drawn at 1x.
SUPERSAMPLE = 4

# Menlo is Apple's DejaVu Sans Mono derivative, so the Linux fallback renders the
# same glyphs at the same advance width.
MONOSPACE_FONTS = [
    ("/System/Library/Fonts/Menlo.ttc", 0, "/System/Library/Fonts/Menlo.ttc", 1),
    (
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        0,
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        0,
    ),
]


def load_font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Load the first available monospace font at ``size``.

    Args:
        size: Font size in pixels.
        bold: Load the bold face instead of regular.

    Returns:
        The loaded font.

    Raises:
        FileNotFoundError: if none of ``MONOSPACE_FONTS`` exists on this machine.
    """
    for regular_path, regular_index, bold_path, bold_index in MONOSPACE_FONTS:
        path, index = (bold_path, bold_index) if bold else (regular_path, regular_index)
        if Path(path).exists():
            return ImageFont.truetype(path, size, index=index)

    raise FileNotFoundError(f"no monospace font found; looked for {MONOSPACE_FONTS}")


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


# The 64x64 mark, matching benchspec-mark.svg unit for unit.
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
    wordmark_font = load_font(64, bold=True)
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
TERMINAL_DOTS = [(24, "#F0764B"), (44, SALMON), (64, GRAY)]
TERMINAL_DOT_RADIUS = 6.5

TERMINAL_TEXT = "#E8ECF1"
TERMINAL_MUTED = "#8B93A7"
TERMINAL_PROMPT = "#F0764B"
TERMINAL_GREEN = "#3FBF7F"
TERMINAL_YELLOW = "#E3BF5F"
TERMINAL_RED = "#E25E59"

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
    bold: bool = False


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


def rate_cell(rate: int, delta_pp: int | None = None, *, bold_rate: bool = False) -> list[Span]:
    """Render one matrix cell: a bare rate for the baseline arm, else ``rate (+Npp)``."""
    spans = [Span(f"{rate}%", color=rate_color(rate), bold=bold_rate)]
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
    header = matrix_line(
        Span("Eval", bold=True), [[Span("baseline", bold=True)], [Span("trial", bold=True)]]
    )
    return [
        TerminalLine([Span(rule, color=TERMINAL_MUTED)], gap_before=REPORT_GAP),
        header,
        matrix_line(Span("hello/greets-by-name"), [rate_cell(33), rate_cell(100, 67)]),
        matrix_line(Span("hello-file/writes-greeting-file"), [rate_cell(50), rate_cell(50, 0)]),
        TerminalLine([Span("-" * TERMINAL_COLUMNS, color=TERMINAL_MUTED)]),
        matrix_line(
            Span("All evals", bold=True), [rate_cell(40), rate_cell(80, 40, bold_rate=True)]
        ),
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
    regular = load_font(TERMINAL_FONT_SIZE)
    bold = load_font(TERMINAL_FONT_SIZE, bold=True)

    line_top = TERMINAL_FIRST_LINE_TOP
    for line in lines:
        line_top += line.gap_before
        cursor_x = TERMINAL_TEXT_LEFT
        for span in line.spans:
            font = bold if span.bold else regular
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
    command = f"{PROJECT_NAME} run"
    typed_lengths = [*range(0, len(command), TYPING_CHARS_PER_FRAME), len(command)]
    frames = [([prompt_line(command[:length])], TYPING_FRAME_MS) for length in typed_lengths]

    prompt = prompt_line(command)
    frames.append(([prompt, progress_line(1, complete=False)], FIRST_PROGRESS_FRAME_MS))
    progress = progress_line(2, complete=True)
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


def main() -> None:
    """Render every raster asset into ``ASSETS_DIR``."""
    frames, durations = render_terminal_frames()
    frames[-1].save(ASSETS_DIR / "benchmark-terminal.png", optimize=True)
    write_gif(ASSETS_DIR / "benchmark-terminal.gif", frames, durations)
    render_social_preview().save(ASSETS_DIR / "social-preview.png", optimize=True)

    for name in ("benchmark-terminal.png", "benchmark-terminal.gif", "social-preview.png"):
        print(f"wrote {ASSETS_DIR / name}")


if __name__ == "__main__":
    main()
