#include "flutter_window.h"

#include <optional>
#include <commdlg.h>
#include <filesystem>
#include <shellapi.h>
#include <flutter/standard_method_codec.h>
#include "utils.h"

#include "flutter/generated_plugin_registrant.h"

FlutterWindow::FlutterWindow(const flutter::DartProject& project)
    : project_(project) {}

FlutterWindow::~FlutterWindow() {}

bool FlutterWindow::OnCreate() {
  if (!Win32Window::OnCreate()) {
    return false;
  }

  RECT frame = GetClientArea();

  // The size here must match the window dimensions to avoid unnecessary surface
  // creation / destruction in the startup path.
  flutter_controller_ = std::make_unique<flutter::FlutterViewController>(
      frame.right - frame.left, frame.bottom - frame.top, project_);
  // Ensure that basic setup of the controller was successful.
  if (!flutter_controller_->engine() || !flutter_controller_->view()) {
    return false;
  }
  RegisterPlugins(flutter_controller_->engine());
  SetChildContent(flutter_controller_->view()->GetNativeWindow());
  design_channel_ = std::make_unique<flutter::MethodChannel<flutter::EncodableValue>>(
      flutter_controller_->engine()->messenger(), "workbench/design",
      &flutter::StandardMethodCodec::GetInstance());
  design_channel_->SetMethodCallHandler([this](const auto& call, auto result) {
    if (call.method_name() == "close") {
      close_approved_ = true;
      result->Success();
      PostMessage(GetHandle(), WM_CLOSE, 0, 0);
      return;
    }
    if (call.method_name() == "openPdf") {
      const auto* value = call.arguments() ? std::get_if<std::string>(call.arguments()) : nullptr;
      if (!value) { result->Error("pdf", "Expected a saved PDF path."); return; }
      const auto path = std::filesystem::u8path(*value);
      std::error_code error;
      if (!path.is_absolute() || _wcsicmp(path.extension().c_str(), L".pdf") != 0 ||
          !std::filesystem::is_regular_file(path, error)) {
        result->Error("pdf", "Choose an existing PDF file."); return;
      }
      const auto opened = reinterpret_cast<INT_PTR>(ShellExecuteW(GetHandle(), L"open", path.c_str(), nullptr, nullptr, SW_SHOWNORMAL));
      if (opened <= 32) result->Error("pdf", "Windows could not open the saved PDF. Choose a PDF viewer and open it manually.");
      else result->Success();
      return;
    }
    if (call.method_name() != "open" && call.method_name() != "save" && call.method_name() != "export" && call.method_name() != "palette" && call.method_name() != "pdf" && call.method_name() != "svg") {
      result->NotImplemented(); return;
    }
    wchar_t filename[32768] = {};
    OPENFILENAMEW dialog{};
    dialog.lStructSize = sizeof(dialog);
    dialog.hwndOwner = GetHandle();
    dialog.lpstrFile = filename;
    dialog.nMaxFile = 32768;
    dialog.Flags = OFN_EXPLORER | OFN_NOCHANGEDIR | OFN_PATHMUSTEXIST;
    const bool svg = call.method_name() == "svg";
    const bool palette = call.method_name() == "palette";
    const bool opening = call.method_name() == "open" || palette || svg;
    const bool dxf = call.method_name() == "export";
    const bool pdf = call.method_name() == "pdf";
    dialog.lpstrFilter = svg ? L"SVG vector artwork\0*.svg\0\0" : palette ? L"Color palettes\0*.json;*.xlsx;*.zip\0CAM palette\0*.json\0Sherwin-Williams workbook or ZIP\0*.xlsx;*.zip\0\0" : opening ? L"CAM jobs and DXF\0*.json;*.dxf\0All files\0*.*\0\0" :
        (pdf ? L"Shop drawing PDF\0*.pdf\0\0" : dxf ? L"DXF drawing\0*.dxf\0\0" : L"CAM job\0*.camjob.json\0\0");
    dialog.lpstrDefExt = svg ? L"svg" : pdf ? L"pdf" : dxf ? L"dxf" : L"camjob.json";
    dialog.lpstrTitle = svg ? L"Import SVG into current drawing" : pdf ? L"Save printable shop drawing" : palette ? L"Import color palette" : opening ? L"Open drawing - source will be preserved" : L"Save a separate Design evaluation copy";
    dialog.Flags |= opening ? OFN_FILEMUSTEXIST : OFN_OVERWRITEPROMPT;
    const BOOL accepted = opening ? GetOpenFileNameW(&dialog) : GetSaveFileNameW(&dialog);
    if (GetForegroundWindow() == GetHandle()) {
      SetFocus(flutter_controller_->view()->GetNativeWindow());
    }
    if (accepted) result->Success(flutter::EncodableValue(Utf8FromUtf16(filename)));
    else if (CommDlgExtendedError() != 0) result->Error("dialog", "Windows file dialog failed.");
    else result->Success();
  });

  flutter_controller_->engine()->SetNextFrameCallback([&]() {
    this->Show();
  });

  // Flutter can complete the first frame before the "show window" callback is
  // registered. The following call ensures a frame is pending to ensure the
  // window is shown. It is a no-op if the first frame hasn't completed yet.
  flutter_controller_->ForceRedraw();

  return true;
}

void FlutterWindow::OnDestroy() {
  design_channel_ = nullptr;
  if (flutter_controller_) {
    flutter_controller_ = nullptr;
  }

  Win32Window::OnDestroy();
}

LRESULT
FlutterWindow::MessageHandler(HWND hwnd, UINT const message,
                              WPARAM const wparam,
                              LPARAM const lparam) noexcept {
  // Give Flutter, including plugins, an opportunity to handle window messages.
  if (flutter_controller_) {
    std::optional<LRESULT> result =
        flutter_controller_->HandleTopLevelWindowProc(hwnd, message, wparam,
                                                      lparam);
    if (result) {
      return *result;
    }
  }

  switch (message) {
    case WM_CLOSE:
      if (!close_approved_ && design_channel_) {
        design_channel_->InvokeMethod("requestClose", nullptr);
        return 0;
      }
      break;
    case WM_FONTCHANGE:
      flutter_controller_->engine()->ReloadSystemFonts();
      break;
  }

  return Win32Window::MessageHandler(hwnd, message, wparam, lparam);
}
