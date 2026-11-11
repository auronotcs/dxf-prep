import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';

const _port = int.fromEnvironment('WORKBENCH_RELOAD_PORT');
const _token = String.fromEnvironment('WORKBENCH_RELOAD_TOKEN');
const developmentReloadEnabled =
    kDebugMode && !kIsWeb && _port > 0 && _token != '';

Future<String> reloadDevelopmentUi({
  int port = _port,
  String token = _token,
}) async {
  final client = HttpClient()..connectionTimeout = const Duration(seconds: 5);
  try {
    final request = await client.postUrl(
      Uri.parse('http://127.0.0.1:$port/reload'),
    );
    request.headers.set(HttpHeaders.authorizationHeader, 'Bearer $token');
    final response = await request.close().timeout(
      const Duration(seconds: 130),
    );
    final body =
        jsonDecode(await utf8.decoder.bind(response).join())
            as Map<String, dynamic>;
    if (response.statusCode != 200 || body['ok'] != true) {
      throw StateError(
        body['message']?.toString() ?? 'Flutter rejected the reload.',
      );
    }
    return body['message'] as String;
  } finally {
    client.close(force: true);
  }
}

class ReloadUiButton extends StatefulWidget {
  const ReloadUiButton({
    super.key,
    this.reload = reloadDevelopmentUi,
    this.enabled = true,
  });
  final Future<String> Function() reload;
  final bool enabled;
  @override
  State<ReloadUiButton> createState() => _ReloadUiButtonState();
}

class _ReloadUiButtonState extends State<ReloadUiButton> {
  bool reloading = false;
  Future<void> reload() async {
    setState(() => reloading = true);
    String message;
    try {
      message = await widget.reload();
    } catch (error) {
      message =
          'UI reload failed: $error. Check build/development.log; no restart was requested.';
    }
    if (!mounted) return;
    setState(() => reloading = false);
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(
        SnackBar(content: Text(message), duration: const Duration(seconds: 8)),
      );
  }

  @override
  Widget build(BuildContext context) => Tooltip(
    message: 'Apply saved Flutter UI edits while keeping the current drawing',
    child: TextButton.icon(
      onPressed: widget.enabled && !reloading ? reload : null,
      icon: reloading
          ? const SizedBox(
              width: 14,
              height: 14,
              child: CircularProgressIndicator(strokeWidth: 2),
            )
          : const Icon(Icons.refresh, size: 17),
      label: Text(
        reloading ? 'Reloading…' : 'Reload UI',
        style: const TextStyle(fontSize: 12),
      ),
    ),
  );
}
