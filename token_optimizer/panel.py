"""A compact translucent popup that is anchored to Claude's composer."""

from time import monotonic

from PySide6.QtCore import QEasingCurve, QPointF, QRect, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QIcon, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                                QPushButton, QScrollArea, QSizePolicy, QSlider, QStyle,
                                QStyleOptionButton, QVBoxLayout, QWidget)

from .engine import Optimization, Options, Strength
from .win32 import monitor_at
from .workflow import Mode


ACCENT = '#D97757'
INK = '#F5EEE6'
MUTED = '#C5B7AA'
TRACK = '#4E3D32'
_LEVELS = ('Léger', 'Équilibré', 'Agressif', 'Maximum')
_GAP = 6
_MARGIN = 9

_STYLE = '''
QWidget { color: #F5EEE6; font-family: "Segoe UI"; font-size: 12px; }
QFrame#inset { background-color: rgba(54, 46, 39, 100); border: 1px solid #463930;
               border-radius: 10px; }
QFrame#rail { background-color: rgba(65, 50, 42, 100); border: 1px solid #493A30;
              border-radius: 8px; }
QLabel#title { font-size: 15px; font-weight: 700; }
QLabel#quiet { color: #C5B7AA; font-size: 11px; }
QLabel#amount { font-size: 20px; font-weight: 700; }
QLabel#caption { font-size: 12px; font-weight: 600; }
QLabel#key { color: #E0C4B5; font-size: 9px; }
QPushButton { border: 1px solid transparent; background: transparent; border-radius: 6px;
              padding: 0 4px; }
QPushButton:focus { border-color: #ED987B; }
QPushButton:hover { background-color: rgba(217, 119, 87, 36); }
QPushButton#tab[active="true"] { background-color: rgba(174, 89, 61, 200); border-color: #AB644E; }
QPushButton#close { font-size: 18px; color: #E3C8B8; }
QPushButton#expand { text-align: left; color: #E8C4B0; background-color: rgba(83, 56, 44, 100);
                    font-size: 11px; padding-left: 7px; }
QPushButton#level { color: #C5B7AA; font-size: 10px; }
QPushButton#level[active="true"] { color: #FFD4C2; font-weight: 700; }
QCheckBox { spacing: 7px; min-height: 18px; }
QCheckBox:focus { border: 1px solid #ED987B; border-radius: 4px; }
QCheckBox::indicator { width: 12px; height: 12px; background: #211E1C;
                      border: 1px solid #766053; border-radius: 3px; }
QCheckBox::indicator:checked { background: #D97757; border-color: #E58C70; }
QSlider::groove:horizontal { height: 3px; background: #544740; border-radius: 1px; }
QSlider::sub-page:horizontal { background: #D97757; border-radius: 1px; }
QSlider::handle:horizontal { width: 9px; height: 9px; background: #241A16; border: 2px solid #E59073;
                            border-radius: 6px; margin: -5px 0; }
QSlider::handle:horizontal:disabled { border-color: #766258; }
QScrollArea { background: transparent; border: 0; }
QScrollBar:vertical { background: transparent; width: 6px; margin: 1px; }
QScrollBar::handle:vertical { background: #685043; min-height: 18px; border-radius: 3px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QLineEdit, QPlainTextEdit { background: #27211D; border: 1px solid #635144; border-radius: 5px;
                          padding: 3px 5px; selection-background-color: #994934; }
QLineEdit:focus, QPlainTextEdit:focus { border-color: #E59073; }
QLabel#notice { color: #FFE5D9; background-color: rgba(83, 57, 46, 175); border-radius: 5px;
                padding: 3px 6px; font-size: 11px; }
'''


def _label(text: str = '', role: str = '') -> QLabel:
    item = QLabel(text)
    item.setObjectName(role)
    item.setTextFormat(Qt.TextFormat.PlainText)
    return item


def _active(widget: QWidget, active: bool) -> None:
    widget.setProperty('active', active)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def _number(value: int) -> str:
    return f'{value:,}'.replace(',', '\u202f')


class Surface(QWidget):
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(105, 81, 66, 210), 1))
        painter.setBrush(QColor(28, 24, 21, 235))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 13, 13)
        painter.end()


class Check(QCheckBox):
    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.isChecked():
            return
        option = QStyleOptionButton()
        self.initStyleOption(option)
        box = self.style().subElementRect(QStyle.SubElement.SE_CheckBoxIndicator, option, self)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor('#25150F'), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                      Qt.PenJoinStyle.RoundJoin))
        x, y = box.center().x(), box.center().y()
        tick = QPainterPath(QPointF(x - 3, y))
        tick.lineTo(x - 0.8, y + 2.5)
        tick.lineTo(x + 3.8, y - 2.7)
        p.drawPath(tick)
        p.end()


class Ring(QWidget):
    def __init__(self, diameter: int, motion: bool, *, counter: bool = False):
        super().__init__()
        self.setFixedSize(diameter, diameter)
        self._counter = counter
        self._motion = motion
        self._mode = 'idle'
        self._value = 0
        self._fraction = 1.0
        self._start = 0.0
        self._frame = QTimer(self, interval=20)
        self._frame.timeout.connect(self.update)
        self._reveal = QVariantAnimation(self, duration=290, startValue=0.0, endValue=1.0)
        self._reveal.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._reveal.valueChanged.connect(self._animate)

    def _animate(self, value):
        self._fraction = float(value)
        self.update()

    def stop(self):
        self._frame.stop()
        self._reveal.stop()
        self._fraction = 1.0
        self.update()

    def state(self, mode: str, value: int = 0):
        self.stop()
        self._mode, self._value = mode, value
        if mode == 'spin':
            self._start = monotonic()
            if self._motion:
                self._frame.start()
        elif mode in ('count', 'result') and self._motion:
            self._fraction = 0.0
            self._reveal.start()
        self.setAccessibleName({'idle': 'Jauge sans mesure', 'spin': 'Calcul local en cours',
                                'count': f'{value} tokens estimés', 'result': f'Variation estimée de {value} %'}[mode])
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        inset, stroke = (6, 7) if self._counter else (4, 5)
        circle = QRectF(self.rect()).adjusted(inset, inset, -inset, -inset)
        p.setPen(QPen(QColor(TRACK), stroke))
        p.drawEllipse(circle)
        if self._mode != 'idle':
            angle = 90.0
            if self._mode == 'spin':
                angle -= ((monotonic() - self._start) * 370) % 360 if self._motion else 0
                span = 88
            elif self._mode == 'count':
                span = 359.8 * self._fraction
            else:
                span = 360 * min(abs(self._value) / 100, 1) * self._fraction
            p.setPen(QPen(QColor(ACCENT), stroke, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            if span > 0:
                p.drawArc(circle, round(16 * angle), -round(16 * span))
        font = QFont('Segoe UI')
        if self._mode == 'count':
            number = _number(round(self._value * self._fraction))
            font.setPixelSize(29 if len(number) <= 4 else 20)
            font.setWeight(QFont.Weight.Bold)
            p.setFont(font)
            p.setPen(QColor(INK))
            p.drawText(QRectF(0, 19, self.width(), 42), Qt.AlignmentFlag.AlignCenter, number)
            font.setWeight(QFont.Weight.Normal)
            font.setPixelSize(10)
            p.setFont(font)
            p.setPen(QColor(MUTED))
            p.drawText(QRectF(0, 56, self.width(), 17), Qt.AlignmentFlag.AlignCenter, 'tokens estimés')
        elif self._mode == 'result':
            sign = '−' if self._value < 0 else '+' if self._value > 0 else ''
            text = f'{sign}{abs(self._value)} %'
            font.setPixelSize(13 if len(text) <= 5 else 10)
            font.setWeight(QFont.Weight.DemiBold)
            p.setFont(font)
            p.setPen(QColor(INK))
            p.drawText(QRectF(self.rect()), Qt.AlignmentFlag.AlignCenter, text)
        elif self._mode == 'idle':
            font.setPixelSize(17)
            p.setFont(font)
            p.setPen(QColor(MUTED))
            p.drawText(QRectF(self.rect()), Qt.AlignmentFlag.AlignCenter, '—')
        p.end()


class Panel(QWidget):
    settings_changed = Signal()
    dismissed = Signal()

    def __init__(self, icon: QIcon, *, motion: bool):
        flags = Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        super().__init__(None, flags)
        self.setWindowTitle('Token Optimizer pour Claude Desktop')
        self.setWindowIcon(icon)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet(_STYLE)
        self._content_width = 390
        self.mode = Mode.COUNT
        self._expanded = False
        self._area: QRect | None = None
        self._anchor: QRect | None = None
        self._native_bounds: tuple[int, int, int, int] | None = None

        # The outer rows are positioned by _fit. Inner layouts only arrange
        # each row's contents and cannot stretch the popup after a tab change.
        self._surface = Surface(self)

        self.header = QWidget(self._surface)
        self.header.setFixedHeight(31)
        header = QHBoxLayout(self.header)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(7)
        mark = _label()
        mark.setFixedSize(25, 25)
        mark.setPixmap(icon.pixmap(25, 25))
        header.addWidget(mark)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        title = _label('Token Optimizer', 'title')
        title.setFixedHeight(17)
        subtitle = _label('pour Claude Desktop', 'quiet')
        subtitle.setFixedHeight(13)
        titles.addWidget(title)
        titles.addWidget(subtitle)
        header.addLayout(titles, 1)
        close = QPushButton('×')
        close.setObjectName('close')
        close.setFixedSize(25, 25)
        close.setAccessibleName('Masquer le panneau')
        close.clicked.connect(self.hide)
        header.addWidget(close)

        self.rail = QFrame(self._surface)
        self.rail.setObjectName('rail')
        self.rail.setFixedHeight(36)
        rail = QHBoxLayout(self.rail)
        rail.setContentsMargins(2, 2, 2, 2)
        rail.setSpacing(2)
        self.tabs = {
            Mode.COUNT: self._tab('Compter', 'Ctrl + Alt + U', Mode.COUNT),
            Mode.OPTIMIZE: self._tab('Optimiser', 'Ctrl + Alt + T', Mode.OPTIMIZE),
        }
        for tab in self.tabs.values():
            rail.addWidget(tab, 1)

        self.count_frame = QFrame(self._surface)
        self.count_frame.setObjectName('inset')
        self.count_frame.setFixedHeight(145)
        count_layout = QVBoxLayout(self.count_frame)
        count_layout.setContentsMargins(6, 4, 6, 5)
        count_layout.setSpacing(2)
        count_layout.addWidget(_label('Brouillon actuel', 'caption'), alignment=Qt.AlignmentFlag.AlignHCenter)
        self.count_ring = Ring(98, motion, counter=True)
        self.count_ring.setToolTip('Estimation du brouillon seulement, avec un vocabulaire Anthropic historique. '
                                   'Ce n’est pas le compte officiel du modèle Claude.')
        count_layout.addWidget(self.count_ring, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.count_hint = _label('Texte inchangé', 'quiet')
        self.count_hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        count_layout.addWidget(self.count_hint)

        self.optimize_frame = QFrame(self._surface)
        self.optimize_frame.setObjectName('inset')
        self.optimize_frame.setFixedHeight(75)
        estimate = QHBoxLayout(self.optimize_frame)
        estimate.setContentsMargins(6, 4, 7, 4)
        estimate.setSpacing(7)
        self.optimize_ring = Ring(60, motion)
        estimate.addWidget(self.optimize_ring)
        metrics = QVBoxLayout()
        metrics.setSpacing(0)
        metrics.addStretch(1)
        self.amount = _label('Prêt à optimiser', 'amount')
        self.amount.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        metrics.addWidget(self.amount)
        self.optimize_hint = _label('Dans le champ de Claude, presse Ctrl + Alt + T.', 'quiet')
        self.optimize_hint.setWordWrap(True)
        metrics.addWidget(self.optimize_hint)
        metrics.addStretch(1)
        estimate.addLayout(metrics, 1)

        self.preview = QPlainTextEdit(self._surface)
        self.preview.setReadOnly(True)
        self.preview.setAccessibleName('Proposition du modèle local, pas encore appliquée')
        self.preview.setFixedHeight(68)
        self.preview.hide()

        self.expand = QPushButton(self._surface)
        self.expand.setObjectName('expand')
        self.expand.setFixedHeight(24)
        self.expand.clicked.connect(self._toggle_settings)

        self.scroll = QScrollArea(self._surface)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.viewport().setAutoFillBackground(False)
        self.scroll.viewport().setStyleSheet('background: transparent;')
        settings = QWidget()
        self.settings_box = QVBoxLayout(settings)
        self.settings_box.setContentsMargins(4, 2, 3, 0)
        self.settings_box.setSpacing(2)
        self.scroll.setWidget(settings)

        self.reduce = self._check('Réduire les tokens', True)
        self.settings_box.addWidget(_label('Niveau de réduction', 'quiet'))
        self.strength = QSlider(Qt.Orientation.Horizontal)
        self.strength.setRange(0, 3)
        self.strength.setValue(1)
        self.strength.setAccessibleName('Niveau de réduction')
        self.strength.setFixedHeight(16)
        self.strength.valueChanged.connect(self._setting)
        ruler = QHBoxLayout()
        ruler.setContentsMargins(22, 0, 22, 0)
        ruler.addWidget(self.strength)
        self.settings_box.addLayout(ruler)
        levels = QHBoxLayout()
        levels.setSpacing(1)
        self.level_buttons = []
        for index, level in enumerate(_LEVELS):
            choice = QPushButton(level)
            choice.setObjectName('level')
            choice.setFixedHeight(18)
            choice.clicked.connect(lambda _checked=False, n=index: self.strength.setValue(n))
            levels.addWidget(choice, 1)
            self.level_buttons.append(choice)
        self.settings_box.addLayout(levels)
        self.clarity = self._check('Clarifier et structurer', True)
        self.examples = self._check('Ajouter des exemples')
        self.example_panel = QWidget()
        fields = QVBoxLayout(self.example_panel)
        fields.setContentsMargins(18, 0, 1, 1)
        fields.setSpacing(3)
        self.example_input = self._field('Exemple d’entrée')
        self.example_output = self._field('Exemple de sortie')
        fields.addWidget(self.example_input)
        fields.addWidget(self.example_output)
        self.settings_box.addWidget(self.example_panel)
        self.agent = self._check('Adapter pour les agents')
        self.custom = self._check('Ajouter des consignes')
        self.custom_panel = QPlainTextEdit()
        self.custom_panel.setPlaceholderText('Ex. pas de tirets, ou : réponds en YAML')
        self.custom_panel.setAccessibleName('Consignes personnelles')
        self.custom_panel.setFixedHeight(53)
        self.custom_panel.document().setMaximumBlockCount(80)
        self.settings_box.addWidget(self.custom_panel)

        self.local_ai = self._check('IA locale · LM Studio')
        self.local_ai.setToolTip('Facultatif · serveur local sur 127.0.0.1:1234, modèle GGUF déjà chargé. '
                                 'Pour le texte simple, sans exemples ni consignes ajoutées. Confirmation avant collage.')

        self.notice = _label('', 'notice')
        self.notice.setParent(self._surface)
        self.notice.setWordWrap(True)
        self.notice.hide()
        self.footer = _label('●  Hors ligne', 'quiet')
        self.footer.setParent(self._surface)
        self.footer.setFixedHeight(14)
        self._surface.show()
        self.header.show()
        self.rail.show()
        self.footer.show()

        self.set_mode(Mode.COUNT)
        self._setting(persist=False)

    def _tab(self, title: str, key: str, mode: Mode) -> QPushButton:
        button = QPushButton()
        button.setObjectName('tab')
        button.setAccessibleName(f'{title}, {key}')
        button.setFixedHeight(30)
        stack = QVBoxLayout(button)
        stack.setContentsMargins(3, 0, 3, 0)
        stack.setSpacing(0)
        for item in (_label(title, 'caption'), _label(key, 'key')):
            item.setAlignment(Qt.AlignmentFlag.AlignCenter)
            item.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            stack.addWidget(item)
        button.clicked.connect(lambda _checked=False: self.set_mode(mode))
        return button

    def _check(self, text: str, checked: bool = False) -> Check:
        item = Check(text)
        item.setChecked(checked)
        item.toggled.connect(self._setting)
        self.settings_box.addWidget(item)
        return item

    @staticmethod
    def _field(title: str) -> QLineEdit:
        field = QLineEdit()
        field.setPlaceholderText(title)
        field.setAccessibleName(title)
        field.setMaxLength(2000)
        return field

    def _setting(self, _unused=None, *, persist: bool = True):
        reduce = self.reduce.isChecked()
        self.strength.setEnabled(reduce)
        level = self.strength.value()
        for position, button in enumerate(self.level_buttons):
            _active(button, reduce and position == level)
            button.setEnabled(reduce)
        self.strength.setAccessibleDescription(_LEVELS[level])
        self.example_panel.setVisible(self.examples.isChecked())
        self.custom_panel.setVisible(self.custom.isChecked())
        self._sync_settings()
        if persist:
            self.settings_changed.emit()

    def _sync_settings(self):
        self.scroll.setVisible(self.mode is Mode.OPTIMIZE and self._expanded)
        level = _LEVELS[self.strength.value()] if self.reduce.isChecked() else 'Réduction désactivée'
        self.expand.setText(('▾' if self._expanded else '▸') + f'  Réglages · {level}')
        self._fit()

    def _toggle_settings(self):
        self._expanded = not self._expanded
        self._sync_settings()

    def options(self) -> Options:
        return Options(reduce=self.reduce.isChecked(), strength=Strength(self.strength.value()),
                       clarify=self.clarity.isChecked(), examples=self.examples.isChecked(),
                       example_input=self.example_input.text(), example_output=self.example_output.text(),
                       agent=self.agent.isChecked(), custom=self.custom.isChecked(),
                       instructions=self.custom_panel.toPlainText()[:5000], local_ai=self.local_ai.isChecked())

    def set_mode(self, mode: Mode):
        self.mode = mode
        counting = mode is Mode.COUNT
        self.count_frame.setVisible(counting)
        self.optimize_frame.setVisible(not counting)
        self.expand.setVisible(not counting)
        self.notice.hide()
        self.clear_preview(refit=False)
        for choice, button in self.tabs.items():
            _active(button, mode is choice)
        self._sync_settings()

    def working(self, mode: Mode):
        self._expanded = False
        self.set_mode(mode)
        self.expand.setEnabled(False)
        self.scroll.setEnabled(False)
        for tab in self.tabs.values():
            tab.setEnabled(False)
        if mode is Mode.COUNT:
            self.count_ring.state('spin')
            self.count_hint.setText('Comptage local…')
        else:
            self.optimize_ring.state('spin')
            self.amount.setText('Optimisation…')
            self.optimize_hint.setText('Sur cet appareil')

    def _settle(self):
        self.expand.setEnabled(True)
        self.scroll.setEnabled(True)
        for tab in self.tabs.values():
            tab.setEnabled(True)

    def show_count(self, count: int):
        self._settle()
        self.count_ring.state('count', count)
        self.count_hint.setText('Texte inchangé' if count else 'Le brouillon est vide')

    def _result_numbers(self, result: Optimization):
        self._settle()
        self.optimize_ring.state('result', result.percent)
        self.amount.setText(f'≈ {_number(result.before)} → {_number(result.after)}')

    def show_result(self, result: Optimization, verified: bool, note: str = ''):
        self._result_numbers(result)
        if verified:
            self.optimize_hint.setText('Sélection remplacée · Ctrl + Z pour annuler')
        elif result.changed:
            self.optimize_hint.setText('Vérifie le texte dans Claude avant de réessayer.')
        else:
            self.optimize_hint.setText('Pas de réduction sûre avec ces réglages.')
        self.notice.setText(note)
        self.notice.setVisible(bool(note))
        self._fit()

    def model_working(self):
        self.optimize_hint.setText('Réécriture sur ton PC…')

    def show_proposal(self, result: Optimization):
        self._result_numbers(result)
        self.optimize_hint.setText('Aperçu IA · Ctrl + Alt + T pour appliquer')
        self.preview.setPlainText(result.text.strip())
        self.preview.show()
        self._fit()

    def clear_preview(self, *, refit: bool = True):
        had_preview = not self.preview.isHidden()
        self.preview.hide()
        self.preview.clear()
        if had_preview:
            self.optimize_ring.state('idle')
            self.amount.setText('Prêt à optimiser')
            self.optimize_hint.setText('Relance Ctrl + Alt + T sur la sélection.')
        if refit:
            self._fit()

    def refused(self, message: str):
        self._settle()
        if self.mode is Mode.COUNT:
            self.count_ring.state('idle')
            self.count_hint.setText('Brouillon inaccessible')
        else:
            self.optimize_ring.state('idle')
            self.amount.setText('Action interrompue')
            self.optimize_hint.setText('Vérifie le champ de Claude.')
        self.notice.setText(message)
        self.notice.show()
        self._fit()

    def reset(self):
        self._settle()
        self._expanded = False
        self.notice.hide()
        self.clear_preview(refit=False)
        self.count_ring.state('idle')
        self.optimize_ring.state('idle')
        self.count_hint.setText('Texte inchangé')
        self.amount.setText('Prêt à optimiser')
        self.optimize_hint.setText('Dans le champ de Claude, presse Ctrl + Alt + T.')
        self._sync_settings()

    def open_at(self, bounds: tuple[int, int, int, int] | None):
        self._place(bounds)
        self.show()  # WA_ShowWithoutActivating preserves the editor's selection.

    def follow(self, bounds: tuple[int, int, int, int] | None):
        if bounds and bounds != self._native_bounds and self.isVisible():
            self._place(bounds)

    def _place(self, bounds: tuple[int, int, int, int] | None):
        """Map Windows screen pixels to the Qt coordinates of the correct monitor."""
        self._native_bounds = bounds
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        monitor = monitor_at(bounds) if bounds else None
        if monitor:
            device, native_x, native_y = monitor
            screens = QGuiApplication.screens()
            exact = next((s for s in screens if s.name().casefold() == device.casefold()), None)
            if exact is None and screens:
                exact = min(screens, key=lambda s: abs(s.geometry().left() - native_x) +
                                                   abs(s.geometry().top() - native_y))
            screen = exact or screen
        self._anchor = None
        self._area = screen.availableGeometry() if screen else None
        if screen:
            ratio = max(1.0, screen.devicePixelRatio())
            if self._area is not None:
                self._content_width = min(self._area.width() - 16, max(220, round(390 / ratio)))
                self.amount.setStyleSheet('font-size: 16px;' if self._content_width < 250 else '')
            if bounds and monitor:
                logical = screen.geometry()
                left, top, right, bottom = bounds
                self._anchor = QRect(logical.left() + round((left - native_x) / ratio),
                                     logical.top() + round((top - native_y) / ratio),
                                     max(1, round((right - left) / ratio)),
                                     max(1, round((bottom - top) / ratio)))
        self._fit()

    def _fit(self):
        counting = self.mode is Mode.COUNT
        width = self._content_width
        inner_width = width - 2 * _MARGIN
        rows = [self.header, self.rail, self.count_frame if counting else self.optimize_frame]
        if not counting:
            if not self.preview.isHidden():
                rows.append(self.preview)
            rows.append(self.expand)
        settings_index = len(rows)
        if not self.notice.isHidden():
            self.notice.setFixedHeight(max(22, self.notice.heightForWidth(inner_width)))
            rows.append(self.notice)
        rows.append(self.footer)
        height = 2 * _MARGIN + sum(row.minimumHeight() for row in rows) + _GAP * (len(rows) - 1)
        area, anchor = self._area, self._anchor
        above = anchor.top() - area.top() - 16 if area and anchor else 350
        below = area.bottom() - anchor.bottom() - 16 if area and anchor else 0
        if not counting and self._expanded:
            self.settings_box.invalidate()
            budget = max(45, (above if above >= height + 65 else max(above, below)) - height - _GAP)
            settings_height = min(235, self.settings_box.sizeHint().height() + 3, budget)
            self.scroll.setFixedHeight(settings_height)
            rows.insert(settings_index, self.scroll)
            height += _GAP + settings_height

        # The shell and row geometry come from the same measurements, not from
        # the previous height negotiated for Optimiser or its expanded settings.
        self.setFixedSize(width, height)
        self._surface.setGeometry(0, 0, width, height)
        y = _MARGIN
        for row in rows:
            row_height = row.minimumHeight()
            row.setGeometry(_MARGIN, y, inner_width, row_height)
            y += row_height + _GAP
        if not area:
            return
        if anchor:
            left = anchor.left() + round(anchor.width() * 0.62) - width // 2
            top = anchor.top() - height - 9
            if top < area.top() + 8 and anchor.bottom() + 9 + height <= area.bottom() - 8:
                top = anchor.bottom() + 9
        else:
            left = area.right() - width - 18
            top = area.bottom() - height - 110
        left = max(area.left() + 8, min(left, area.right() - width - 7))
        top = max(area.top() + 8, min(top, area.bottom() - height - 7))
        self.move(left, top)

    def hideEvent(self, event):
        super().hideEvent(event)
        self.count_ring.stop()
        self.optimize_ring.stop()
        self.dismissed.emit()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(event)
