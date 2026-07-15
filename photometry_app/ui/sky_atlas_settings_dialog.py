from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from photometry_app.app_metadata import application_install_path
from photometry_app.core.settings import AppSettings
from photometry_app.core.sky_atlas import SKY_ATLAS_DEEP_SKY_CATALOG_NAMES, normalize_sky_atlas_deep_sky_catalogs
from photometry_app.core.sky_atlas_catalog_storage import (
    estimate_star_catalog_download,
    format_data_size_mb,
    format_star_count,
    sky_atlas_catalog_root,
)


@dataclass(frozen=True, slots=True)
class SkyAtlasDisplaySettings:
    star_magnitude_limit: float = 8.7
    star_brightness: float = 1.0
    star_size: float = 1.0
    star_magnitude_size_contrast: float = 1.0
    star_twinkle: float = 0.5
    show_messier: bool = True
    show_ngc: bool = True
    show_ic: bool = False
    show_vdb: bool = False
    show_ldn: bool = False
    show_lbn: bool = False
    object_magnitude_limit: float = 10.0
    object_label_magnitude_limit: float = 8.0

    @classmethod
    def from_app_settings(cls, settings: AppSettings) -> SkyAtlasDisplaySettings:
        return cls(
            star_magnitude_limit=float(settings.sky_atlas_star_magnitude_limit),
            star_brightness=float(settings.sky_atlas_star_brightness),
            star_size=float(settings.sky_atlas_star_size),
            star_magnitude_size_contrast=float(settings.sky_atlas_star_magnitude_size_contrast),
            star_twinkle=float(settings.sky_atlas_star_twinkle),
            show_messier=bool(settings.sky_atlas_show_messier),
            show_ngc=bool(settings.sky_atlas_show_ngc),
            show_ic=bool(settings.sky_atlas_show_ic),
            show_vdb=bool(settings.sky_atlas_show_vdb),
            show_ldn=bool(settings.sky_atlas_show_ldn),
            show_lbn=bool(settings.sky_atlas_show_lbn),
            object_magnitude_limit=float(settings.sky_atlas_object_magnitude_limit),
            object_label_magnitude_limit=float(settings.sky_atlas_object_label_magnitude_limit),
        )

    def enabled_deep_sky_catalogs(self) -> frozenset[str]:
        enabled: set[str] = set()
        if self.show_messier:
            enabled.add("Messier")
        if self.show_ngc:
            enabled.add("NGC")
        if self.show_ic:
            enabled.add("IC")
        if self.show_vdb:
            enabled.add("VdB")
        if self.show_ldn:
            enabled.add("LDN")
        if self.show_lbn:
            enabled.add("LBN")
        return normalize_sky_atlas_deep_sky_catalogs(enabled)


class SkyAtlasSettingsDialog(QDialog):
    """Sky Atlas display settings: stars and deep-sky object catalogs."""

    settingsChanged = Signal(object)

    def __init__(
        self,
        settings: AppSettings | SkyAtlasDisplaySettings,
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sky Atlas Settings")
        self.setModal(True)
        self.resize(580, 560)

        if isinstance(settings, AppSettings):
            display_settings = SkyAtlasDisplaySettings.from_app_settings(settings)
        else:
            display_settings = settings

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(14, 14, 14, 14)
        root_layout.setSpacing(12)

        intro = QLabel("Adjust what stars and deep-sky objects appear in Sky Atlas.")
        intro.setWordWrap(True)
        intro.setObjectName("skyAtlasSettingsIntro")
        root_layout.addWidget(intro)

        body = QHBoxLayout()
        body.setSpacing(12)
        root_layout.addLayout(body, stretch=1)

        self._section_list = QListWidget()
        self._section_list.setObjectName("skyAtlasSettingsNav")
        self._section_list.setFixedWidth(148)
        self._section_list.setSpacing(2)
        for label in ("Stars", "Deep-Sky Objects"):
            QListWidgetItem(label, self._section_list)
        self._section_list.setCurrentRow(0)
        body.addWidget(self._section_list)

        self._pages = QStackedWidget()
        body.addWidget(self._pages, stretch=1)

        self._pages.addWidget(self._build_stars_page(display_settings))
        self._pages.addWidget(self._build_objects_page(display_settings))
        self._section_list.currentRowChanged.connect(self._pages.setCurrentIndex)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.RestoreDefaults
            | QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(self._restore_defaults)
        root_layout.addWidget(buttons)

        self._emit_settings_changed()

    def current_settings(self) -> SkyAtlasDisplaySettings:
        return SkyAtlasDisplaySettings(
            star_magnitude_limit=self._star_magnitude_limit_slider.value() / 10.0,
            star_brightness=self._star_brightness_slider.value() / 100.0,
            star_size=self._star_size_slider.value() / 100.0,
            star_magnitude_size_contrast=self._star_contrast_slider.value() / 100.0,
            star_twinkle=self._star_twinkle_slider.value() / 100.0,
            show_messier=self._catalog_checks["Messier"].isChecked(),
            show_ngc=self._catalog_checks["NGC"].isChecked(),
            show_ic=self._catalog_checks["IC"].isChecked(),
            show_vdb=self._catalog_checks["VdB"].isChecked(),
            show_ldn=self._catalog_checks["LDN"].isChecked(),
            show_lbn=self._catalog_checks["LBN"].isChecked(),
            object_magnitude_limit=self._object_magnitude_limit_slider.value() / 10.0,
            object_label_magnitude_limit=self._object_label_limit_slider.value() / 10.0,
        )

    def _build_stars_page(self, settings: SkyAtlasDisplaySettings) -> QWidget:
        page = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(4, 4, 12, 4)
        layout.setSpacing(18)

        self._star_magnitude_limit_slider, star_mag_row, self._star_magnitude_limit_value = self._magnitude_slider_row(
            minimum=30,
            maximum=150,
            value=int(round(settings.star_magnitude_limit * 10.0)),
            decimals=1,
        )
        layout.addWidget(
            self._setting_block(
                "Limiting magnitude",
                "Fainter stars fade out beyond this value. Up to 15.0 is supported.",
                star_mag_row,
            )
        )

        catalog_box = QFrame()
        catalog_box.setObjectName("skyAtlasCatalogEstimate")
        catalog_layout = QVBoxLayout(catalog_box)
        catalog_layout.setContentsMargins(10, 10, 10, 10)
        catalog_layout.setSpacing(6)
        catalog_title = QLabel("Star catalog download")
        catalog_title.setStyleSheet("font-weight: 600;")
        catalog_layout.addWidget(catalog_title)
        self._catalog_estimate_label = QLabel()
        self._catalog_estimate_label.setWordWrap(True)
        catalog_layout.addWidget(self._catalog_estimate_label)
        self._catalog_path_label = QLabel(f"Saved under: {sky_atlas_catalog_root(application_install_path())}")
        self._catalog_path_label.setWordWrap(True)
        self._catalog_path_label.setStyleSheet("color: #94a3b8;")
        catalog_layout.addWidget(self._catalog_path_label)
        estimate_hint = QLabel("Click OK to download any missing catalog data for the selected magnitude.")
        estimate_hint.setWordWrap(True)
        estimate_hint.setStyleSheet("color: #94a3b8;")
        catalog_layout.addWidget(estimate_hint)
        layout.addWidget(catalog_box)
        self._star_magnitude_limit_slider.valueChanged.connect(self._refresh_catalog_estimate)
        self._refresh_catalog_estimate()

        self._star_brightness_slider, star_brightness_row, self._star_brightness_value = self._ratio_slider_row(
            minimum=35,
            maximum=200,
            value=int(round(settings.star_brightness * 100.0)),
            suffix="x",
        )
        layout.addWidget(self._setting_block("Star brightness", "Overall glow intensity.", star_brightness_row))

        self._star_size_slider, star_size_row, self._star_size_value = self._ratio_slider_row(
            minimum=40,
            maximum=250,
            value=int(round(settings.star_size * 100.0)),
            suffix="x",
        )
        layout.addWidget(self._setting_block("Star size", "Base disc size for all stars.", star_size_row))

        self._star_contrast_slider, star_contrast_row, self._star_contrast_value = self._ratio_slider_row(
            minimum=0,
            maximum=200,
            value=int(round(settings.star_magnitude_size_contrast * 100.0)),
            suffix="x",
        )
        layout.addWidget(
            self._setting_block(
                "Magnitude size contrast",
                "How strongly fainter stars shrink relative to bright ones.",
                star_contrast_row,
            )
        )

        self._star_twinkle_slider, star_twinkle_row, self._star_twinkle_value = self._percent_slider_row(
            value=int(round(settings.star_twinkle * 100.0)),
        )
        layout.addWidget(self._setting_block("Twinkling", "Atmospheric scintillation amount.", star_twinkle_row))
        layout.addStretch(1)

        scroll.setWidget(content)
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.addWidget(scroll)
        return page

    def _build_objects_page(self, settings: SkyAtlasDisplaySettings) -> QWidget:
        page = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(4, 4, 12, 4)
        layout.setSpacing(14)

        catalogs_label = QLabel("Catalogs to show")
        catalogs_label.setObjectName("skyAtlasSettingsSectionTitle")
        layout.addWidget(catalogs_label)
        catalogs_hint = QLabel("Enable the deep-sky catalogs you want plotted on the sky.")
        catalogs_hint.setWordWrap(True)
        catalogs_hint.setStyleSheet("color: #94a3b8;")
        layout.addWidget(catalogs_hint)

        self._catalog_checks: dict[str, QCheckBox] = {}
        catalog_states = {
            "Messier": settings.show_messier,
            "NGC": settings.show_ngc,
            "IC": settings.show_ic,
            "VdB": settings.show_vdb,
            "LDN": settings.show_ldn,
            "LBN": settings.show_lbn,
        }
        catalog_descriptions = {
            "Messier": "M1–M110 showpieces",
            "NGC": "New General Catalogue",
            "IC": "Index Catalogue",
            "VdB": "van den Bergh reflection nebulae",
            "LDN": "Lynds Dark Nebulae",
            "LBN": "Lynds Bright Nebulae",
        }
        for catalog_name in SKY_ATLAS_DEEP_SKY_CATALOG_NAMES:
            checkbox = QCheckBox(f"{catalog_name}  —  {catalog_descriptions[catalog_name]}")
            checkbox.setChecked(bool(catalog_states[catalog_name]))
            checkbox.toggled.connect(self._emit_settings_changed)
            self._catalog_checks[catalog_name] = checkbox
            layout.addWidget(checkbox)

        layout.addSpacing(8)
        limits_label = QLabel("Visibility")
        limits_label.setObjectName("skyAtlasSettingsSectionTitle")
        layout.addWidget(limits_label)

        self._object_magnitude_limit_slider, object_mag_row, self._object_magnitude_limit_value = self._magnitude_slider_row(
            minimum=10,
            maximum=160,
            value=int(round(settings.object_magnitude_limit * 10.0)),
            decimals=1,
        )
        layout.addWidget(
            self._setting_block(
                "Object limiting magnitude",
                "Marks objects brighter than this limit.",
                object_mag_row,
            )
        )

        self._object_label_limit_slider, object_label_row, self._object_label_limit_value = self._magnitude_slider_row(
            minimum=10,
            maximum=160,
            value=int(round(settings.object_label_magnitude_limit * 10.0)),
            decimals=1,
        )
        layout.addWidget(
            self._setting_block(
                "Label limiting magnitude",
                "Shows names only for objects brighter than this.",
                object_label_row,
            )
        )
        layout.addStretch(1)

        scroll.setWidget(content)
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.addWidget(scroll)
        return page

    def _setting_block(self, title: str, hint: str, control: QWidget) -> QWidget:
        block = QWidget()
        layout = QVBoxLayout(block)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        title_label = QLabel(title)
        title_label.setWordWrap(True)
        hint_label = QLabel(hint)
        hint_label.setWordWrap(True)
        hint_label.setStyleSheet("color: #94a3b8;")
        layout.addWidget(title_label)
        layout.addWidget(hint_label)
        layout.addWidget(control)
        return block

    def _magnitude_slider_row(
        self,
        *,
        minimum: int,
        maximum: int,
        value: int,
        decimals: int,
    ) -> tuple[QSlider, QWidget, QLabel]:
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(minimum, maximum)
        slider.setValue(value)
        value_label = QLabel()
        value_label.setMinimumWidth(44)
        value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        def _sync(current: int) -> None:
            value_label.setText(f"{current / 10.0:.{decimals}f}")
            self._emit_settings_changed()

        slider.valueChanged.connect(_sync)
        _sync(slider.value())
        return slider, self._slider_row(slider, value_label), value_label

    def _ratio_slider_row(
        self,
        *,
        minimum: int,
        maximum: int,
        value: int,
        suffix: str,
    ) -> tuple[QSlider, QWidget, QLabel]:
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(minimum, maximum)
        slider.setValue(value)
        value_label = QLabel()
        value_label.setMinimumWidth(52)
        value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        def _sync(current: int) -> None:
            value_label.setText(f"{current / 100.0:.2f}{suffix}")
            self._emit_settings_changed()

        slider.valueChanged.connect(_sync)
        _sync(slider.value())
        return slider, self._slider_row(slider, value_label), value_label

    def _percent_slider_row(self, *, value: int) -> tuple[QSlider, QWidget, QLabel]:
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(0, 100)
        slider.setValue(value)
        value_label = QLabel()
        value_label.setMinimumWidth(44)
        value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        def _sync(current: int) -> None:
            value_label.setText(f"{current}%")
            self._emit_settings_changed()

        slider.valueChanged.connect(_sync)
        _sync(slider.value())
        return slider, self._slider_row(slider, value_label), value_label

    @staticmethod
    def _slider_row(slider: QSlider, value_label: QLabel) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(slider, stretch=1)
        layout.addWidget(value_label)
        return row

    def _refresh_catalog_estimate(self) -> None:
        if not hasattr(self, "_catalog_estimate_label"):
            return
        estimate = estimate_star_catalog_download(self._star_magnitude_limit_slider.value() / 10.0)
        self._catalog_estimate_label.setText(
            f"{estimate.catalog_label}: about {format_star_count(estimate.approximate_star_count)} stars "
            f"({format_data_size_mb(estimate.approximate_size_mb)}). {estimate.notes}."
        )

    def _restore_defaults(self) -> None:
        defaults = SkyAtlasDisplaySettings()
        self._star_magnitude_limit_slider.setValue(int(round(defaults.star_magnitude_limit * 10.0)))
        self._star_brightness_slider.setValue(int(round(defaults.star_brightness * 100.0)))
        self._star_size_slider.setValue(int(round(defaults.star_size * 100.0)))
        self._star_contrast_slider.setValue(int(round(defaults.star_magnitude_size_contrast * 100.0)))
        self._star_twinkle_slider.setValue(int(round(defaults.star_twinkle * 100.0)))
        self._catalog_checks["Messier"].setChecked(defaults.show_messier)
        self._catalog_checks["NGC"].setChecked(defaults.show_ngc)
        self._catalog_checks["IC"].setChecked(defaults.show_ic)
        self._catalog_checks["VdB"].setChecked(defaults.show_vdb)
        self._catalog_checks["LDN"].setChecked(defaults.show_ldn)
        self._catalog_checks["LBN"].setChecked(defaults.show_lbn)
        self._object_magnitude_limit_slider.setValue(int(round(defaults.object_magnitude_limit * 10.0)))
        self._object_label_limit_slider.setValue(int(round(defaults.object_label_magnitude_limit * 10.0)))

    def _emit_settings_changed(self) -> None:
        required = (
            "_star_magnitude_limit_slider",
            "_star_brightness_slider",
            "_star_size_slider",
            "_star_contrast_slider",
            "_star_twinkle_slider",
            "_catalog_checks",
            "_object_magnitude_limit_slider",
            "_object_label_limit_slider",
        )
        if any(not hasattr(self, name) for name in required):
            return
        self.settingsChanged.emit(self.current_settings())
