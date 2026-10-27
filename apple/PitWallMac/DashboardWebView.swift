import SwiftUI
import WebKit

/// The engine's dashboard (dashboard.html on 127.0.0.1) in a WKWebView,
/// with the browser behaviours it relies on: Stripe links open in the
/// default browser, the CSV export saves through a save panel, and the
/// Talk button can use the microphone.
struct DashboardWebView: NSViewRepresentable {
    let url: URL
    @Binding var loaded: Bool

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeNSView(context: Context) -> WKWebView {
        let web = WKWebView(frame: .zero, configuration: WKWebViewConfiguration())
        web.navigationDelegate = context.coordinator
        web.uiDelegate = context.coordinator
        web.underPageBackgroundColor = NSColor(dashboardBackground)
        web.setValue(false, forKey: "drawsBackground")
        web.allowsBackForwardNavigationGestures = false
        #if DEBUG
        web.isInspectable = true
        #endif
        context.coordinator.attach(web)
        web.load(URLRequest(url: url))
        return web
    }

    func updateNSView(_ web: WKWebView, context: Context) {
        context.coordinator.parent = self
    }

    @MainActor
    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
        var parent: DashboardWebView
        private weak var web: WKWebView?

        init(_ parent: DashboardWebView) { self.parent = parent }

        func attach(_ web: WKWebView) {
            self.web = web
            NotificationCenter.default.addObserver(
                self, selector: #selector(reload), name: .reloadDashboard, object: nil)
        }

        @objc private func reload() { web?.reload() }

        private func isDashboard(_ url: URL?) -> Bool {
            guard let url else { return true }
            if ["about", "blob", "data"].contains(url.scheme) { return true }
            return url.host == parent.url.host && url.port == parent.url.port
        }

        // MARK: Navigation

        func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                     decisionHandler: @escaping @MainActor (WKNavigationActionPolicy) -> Void) {
            if action.shouldPerformDownload {
                decisionHandler(.download)
            } else if isDashboard(action.request.url) {
                decisionHandler(.allow)
            } else {
                // Stripe Checkout / Customer Portal and any other outside
                // link: the user's own browser, not this window.
                if let url = action.request.url { NSWorkspace.shared.open(url) }
                decisionHandler(.cancel)
            }
        }

        func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse,
                     decisionHandler: @escaping @MainActor (WKNavigationResponsePolicy) -> Void) {
            decisionHandler(response.canShowMIMEType ? .allow : .download)
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            parent.loaded = true
            #if DEBUG
            // PITWALL_SNAPSHOT=/path.png writes what the window shows a few
            // seconds after load, for checking the app without a screen.
            if let path = ProcessInfo.processInfo.environment["PITWALL_SNAPSHOT"] {
                DispatchQueue.main.asyncAfter(deadline: .now() + 5) { [weak webView] in
                    webView?.takeSnapshot(with: nil) { image, _ in
                        guard let tiff = image?.tiffRepresentation,
                              let png = NSBitmapImageRep(data: tiff)?.representation(using: .png, properties: [:])
                        else { return }
                        try? png.write(to: URL(fileURLWithPath: path))
                    }
                }
            }
            #endif
        }

        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!,
                     withError error: Error) {
            // The engine is up (Engine checked) but may still be binding;
            // try again shortly rather than leave a blank window.
            DispatchQueue.main.asyncAfter(deadline: .now() + 1) { [weak webView, url = parent.url] in
                webView?.load(URLRequest(url: url))
            }
        }

        func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
            webView.reload()
        }

        // MARK: UI

        func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                     for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
            if let url = action.request.url { NSWorkspace.shared.open(url) }
            return nil
        }

        func webView(_ webView: WKWebView, requestMediaCapturePermissionFor origin: WKSecurityOrigin,
                     initiatedByFrame frame: WKFrameInfo, type: WKMediaCaptureType,
                     decisionHandler: @escaping @MainActor (WKPermissionDecision) -> Void) {
            decisionHandler(origin.host == parent.url.host ? .grant : .deny)
        }

        // MARK: Downloads (CSV export)

        func webView(_ webView: WKWebView, navigationAction: WKNavigationAction,
                     didBecome download: WKDownload) {
            download.delegate = self
        }

        func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse,
                     didBecome download: WKDownload) {
            download.delegate = self
        }

        func download(_ download: WKDownload, decideDestinationUsing response: URLResponse,
                      suggestedFilename: String) async -> URL? {
            let panel = NSSavePanel()
            panel.nameFieldStringValue = suggestedFilename
            panel.directoryURL = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask).first
            guard panel.runModal() == .OK, let dest = panel.url else { return nil }
            // The panel already confirmed any overwrite; WebKit refuses to
            // write over an existing file.
            try? FileManager.default.removeItem(at: dest)
            return dest
        }
    }
}
