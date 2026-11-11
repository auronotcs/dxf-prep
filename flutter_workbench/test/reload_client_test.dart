import 'dart:convert';
import 'dart:io';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/reload_ui.dart';

void main() {
  test('Reload client authenticates and reports compiler rejection', () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    addTearDown(() => server.close(force: true));
    var calls = 0;
    server.listen((request) async {
      expect(request.method, 'POST');
      expect(request.uri.path, '/reload');
      expect(
        request.headers.value(HttpHeaders.authorizationHeader),
        'Bearer secret',
      );
      calls++;
      request.response.statusCode = calls == 1 ? 200 : 422;
      request.response.write(
        jsonEncode({
          'ok': calls == 1,
          'message': calls == 1 ? 'UI reloaded' : 'Compile error',
        }),
      );
      await request.response.close();
    });
    expect(
      await reloadDevelopmentUi(port: server.port, token: 'secret'),
      'UI reloaded',
    );
    await expectLater(
      reloadDevelopmentUi(port: server.port, token: 'secret'),
      throwsA(isA<StateError>()),
    );
  });
}
