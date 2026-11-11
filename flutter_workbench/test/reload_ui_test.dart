import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/reload_ui.dart';

void main() {
  testWidgets('Reload button prevents duplicates and recovers after failure', (
    tester,
  ) async {
    var calls = 0;
    final pending = Completer<String>();
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ReloadUiButton(
            reload: () {
              calls++;
              return pending.future;
            },
          ),
        ),
      ),
    );
    await tester.tap(find.text('Reload UI'));
    await tester.pump();
    expect(find.text('Reloading…'), findsOneWidget);
    expect(
      tester.widget<TextButton>(find.byType(TextButton)).onPressed,
      isNull,
    );
    expect(calls, 1);
    pending.completeError(StateError('Compile error'));
    await tester.pumpAndSettle();
    expect(find.text('Reload UI'), findsOneWidget);
    expect(find.textContaining('Compile error'), findsOneWidget);
    expect(
      tester.widget<TextButton>(find.byType(TextButton)).onPressed,
      isNotNull,
    );
  });
}
