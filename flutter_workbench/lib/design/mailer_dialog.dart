import 'package:flutter/material.dart';
import 'controller.dart';
import 'canvas.dart' show entityPath;

const mailerFields = {
  'length': 'Base length, fold to fold',
  'width': 'Base width, fold to fold',
  'height': 'Wall height, fold to fold',
  'thickness': 'Cardboard thickness',
  'clearance': 'Extra fit clearance',
  'glue_tab': 'Glue / tape tab depth',
  'dust_flap': 'Lid dust-flap depth',
  'tuck_flap': 'Front tuck-flap depth',
  'score_inset': 'Score endpoint inset',
  'x': 'Blank lower-left X',
  'y': 'Blank lower-left Y',
};

class MailerDialog extends StatefulWidget {
  const MailerDialog({super.key, required this.model});
  final DesignController model;
  @override
  State<MailerDialog> createState() => _MailerDialogState();
}

class _MailerDialogState extends State<MailerDialog> {
  DesignController get m => widget.model;
  late final Object? revision;
  final fields = {
    for (final key in mailerFields.keys) key: TextEditingController(),
  };
  final camera = TransformationController();
  final previewAnchor = GlobalKey();
  Json? data, preview;
  String? error;
  bool busy = false,
      stale = false,
      rotate = false,
      reviewed = false,
      placementReviewed = false;
  @override
  void initState() {
    super.initState();
    revision = m.state['revision'];
    m.addListener(changed);
    begin();
  }

  @override
  void dispose() {
    m.removeListener(changed);
    for (final c in fields.values) {
      c.dispose();
    }
    camera.dispose();
    super.dispose();
  }

  void changed() {
    if (m.state['revision'] != revision || !m.ready) {
      setState(() {
        stale = true;
        preview = null;
        reviewed = placementReviewed = false;
        error = 'Drawing changed. Close and reopen the mailer review.';
      });
    }
  }

  void edit(VoidCallback f) => setState(() {
    f();
    preview = null;
    reviewed = placementReviewed = false;
    error = null;
    camera.value = Matrix4.identity();
  });
  Future<void> begin() async {
    setState(() => busy = true);
    try {
      final result = await m.request('design.mailer_begin');
      if (result['ok'] != true) throw StateError('${result['error']}');
      if (!mounted || stale) return;
      data = result['context'] as Json;
      for (final entry in fields.entries) {
        entry.value.text = '${data!['defaults'][entry.key]}';
      }
      rotate = data!['defaults']['rotate'] == true;
      await calculate(reveal: false);
    } catch (e) {
      if (mounted) setState(() => error = '$e');
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> calculate({bool reveal = true}) async {
    setState(() {
      busy = true;
      preview = null;
      reviewed = placementReviewed = false;
      error = null;
      camera.value = Matrix4.identity();
    });
    try {
      final options = <String, dynamic>{'rotate': rotate};
      for (final entry in fields.entries) {
        final value = double.tryParse(entry.value.text);
        if (value == null || !value.isFinite) {
          throw StateError('Enter finite dimensions in ${m.units}.');
        }
        options[entry.key] = value;
      }
      final result = await m.request('design.mailer_preview', {
        'context_token': data!['context_token'],
        'options': options,
      });
      if (result['ok'] != true) throw StateError('${result['error']}');
      if (mounted && !stale) {
        setState(() => preview = result['preview'] as Json);
        if (reveal) {
          WidgetsBinding.instance.addPostFrameCallback((_) {
            final target = previewAnchor.currentContext;
            if (mounted && target != null) {
              Scrollable.ensureVisible(
                target,
                alignment: .1,
                duration: const Duration(milliseconds: 160),
              );
            }
          });
        }
      }
    } catch (e) {
      if (mounted) setState(() => error = '$e');
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> apply() async {
    final token = preview!['token'];
    setState(() {
      busy = true;
      error = null;
    });
    final ok = await m.run('design.mailer_apply', {
      'token': token,
      'reviewed': reviewed,
      'placement_reviewed': placementReviewed,
    });
    if (!mounted) return;
    if (ok) {
      Navigator.pop(context, true);
    } else {
      setState(() {
        busy = false;
        preview = null;
        reviewed = placementReviewed = false;
        error = m.message;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final active = !busy && !stale;
    final warnings = (preview?['warnings'] as List? ?? []);
    final box = preview?['bounds'] as List?;
    return PopScope(
      canPop: !busy,
      child: AlertDialog(
        title: const Text('Hinged-mailer box'),
        content: SizedBox(
          width: 1000,
          child: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text(
                  'Glue/tape-assisted tray with an inset hinged lid. Dimensions are measured between folds, not guaranteed finished inside dimensions. Test-cut and fold one blank first.',
                ),
                if (busy) const LinearProgressIndicator(),
                if (error != null)
                  Text(
                    error!,
                    style: TextStyle(
                      color: Theme.of(context).colorScheme.error,
                    ),
                  ),
                if (data != null) ...[
                  Wrap(
                    spacing: 12,
                    runSpacing: 8,
                    children: [
                      for (final entry in mailerFields.entries)
                        SizedBox(
                          width: 265,
                          child: TextField(
                            controller: fields[entry.key],
                            enabled: active,
                            decoration: InputDecoration(
                              labelText: entry.value,
                              suffixText: m.units,
                            ),
                            onChanged: (_) => edit(() {}),
                          ),
                        ),
                    ],
                  ),
                  Wrap(
                    crossAxisAlignment: WrapCrossAlignment.center,
                    children: [
                      SizedBox(
                        width: 260,
                        child: CheckboxListTile(
                          contentPadding: EdgeInsets.zero,
                          value: rotate,
                          title: const Text('Rotate blank 90°'),
                          onChanged: active
                              ? (v) => edit(() => rotate = v == true)
                              : null,
                        ),
                      ),
                      TextButton(
                        onPressed: active && data!['plate'] != null
                            ? () => edit(() {
                                fields['x']!.text = '${data!['defaults']['x']}';
                                fields['y']!.text = '${data!['defaults']['y']}';
                              })
                            : null,
                        child: const Text('Use active plate margin'),
                      ),
                    ],
                  ),
                  const Text(
                    'Thickness plus extra clearance provides flap relief and lid inset. Glue tabs fold inside the side walls. Dust flaps and the front tuck fold inside the tray; no locking slots are generated.',
                  ),
                ],
                if (preview != null && box != null) ...[
                  const SizedBox(height: 10),
                  Text(
                    'Blank ${(number(box[2]) - number(box[0])).toStringAsFixed(4)} × ${(number(box[3]) - number(box[1])).toStringAsFixed(4)} ${m.units} · ${preview!['cut_count']} closed cut outline · ${preview!['score_count']} open score lines',
                  ),
                  Row(
                    children: [
                      const Expanded(
                        child: Text(
                          'Teal solid: cut outline · Orange dashed: shallow scores',
                        ),
                      ),
                      TextButton(
                        onPressed: () =>
                            setState(() => camera.value = Matrix4.identity()),
                        child: const Text('Fit preview'),
                      ),
                    ],
                  ),
                  LayoutBuilder(
                    builder: (context, c) => SizedBox(
                      key: previewAnchor,
                      height: 330,
                      child: InteractiveViewer(
                        transformationController: camera,
                        minScale: .5,
                        maxScale: 20,
                        child: SizedBox(
                          width: c.maxWidth,
                          height: 330,
                          child: CustomPaint(
                            key: const ValueKey('mailer-preview'),
                            painter: MailerPainter(preview!),
                          ),
                        ),
                      ),
                    ),
                  ),
                  if (warnings.isEmpty)
                    const Text(
                      'Blank fits the selected sheet bounding box. Knife lead/loop and holder clearance still require checking.',
                    ),
                  for (final warning in warnings)
                    Padding(
                      padding: const EdgeInsets.only(top: 8),
                      child: Text('$warning'),
                    ),
                  const Padding(
                    padding: EdgeInsets.only(top: 8),
                    child: Text(
                      'Creates BOX_SCORE and BOX_CUT geometry only. In Manufacturing, review a shallow score/engrave depth and feed and score FIRST, then assign the outline through-cut. No toolpaths or output approval are created.',
                    ),
                  ),
                  CheckboxListTile(
                    contentPadding: EdgeInsets.zero,
                    value: reviewed,
                    onChanged: active
                        ? (v) => setState(() => reviewed = v == true)
                        : null,
                    title: const Text(
                      'I will test fold fit and review score depth, hold-down and cutter clearance',
                    ),
                  ),
                  if (warnings.isNotEmpty)
                    CheckboxListTile(
                      contentPadding: EdgeInsets.zero,
                      value: placementReviewed,
                      onChanged: active
                          ? (v) => setState(() => placementReviewed = v == true)
                          : null,
                      title: const Text(
                        'I reviewed the placement findings; add geometry for layout review',
                      ),
                    ),
                ],
              ],
            ),
          ),
        ),
        actions: [
          TextButton(
            onPressed: busy ? null : () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          OutlinedButton(
            onPressed: active && data != null ? () => calculate() : null,
            child: const Text('Preview blank'),
          ),
          FilledButton(
            onPressed:
                active &&
                    preview != null &&
                    reviewed &&
                    (warnings.isEmpty || placementReviewed)
                ? apply
                : null,
            child: const Text('Create box geometry'),
          ),
        ],
      ),
    );
  }
}

class MailerPainter extends CustomPainter {
  MailerPainter(this.preview);
  final Json preview;
  @override
  void paint(Canvas canvas, Size size) {
    canvas.save();
    canvas.clipRect(Offset.zero & size);
    canvas.drawColor(Colors.white, BlendMode.src);
    final b = bounds(preview['bounds'])!;
    final sx = (size.width - 32) / b.width, sy = (size.height - 32) / b.height;
    final scale = sx < sy ? sx : sy;
    canvas.translate(size.width / 2, size.height / 2);
    canvas.scale(scale, -scale);
    canvas.translate(-b.center.dx, -b.center.dy);
    for (final e in (preview['entities'] as List).cast<Json>()) {
      final path = entityPath(e);
      final cut = e['metadata']['box_role'] == 'cut';
      final paint = Paint()
        ..color = cut ? const Color(0xff008577) : const Color(0xffb45d00)
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1.3 / scale;
      if (cut) {
        canvas.drawPath(path, Paint()..color = const Color(0xfff7f6ed));
        canvas.drawPath(path, paint);
      } else {
        for (final metric in path.computeMetrics()) {
          for (double start = 0; start < metric.length; start += 9 / scale) {
            canvas.drawPath(
              metric.extractPath(
                start,
                (start + 5 / scale).clamp(0, metric.length),
              ),
              paint,
            );
          }
        }
      }
    }
    canvas.restore();
    for (final label in (preview['labels'] as List).cast<Json>().where(
      (e) => ['Base', 'Lid', 'Front', 'Back / hinge'].contains(e['text']),
    )) {
      final text = TextPainter(
        text: TextSpan(
          text: label['text'],
          style: const TextStyle(
            color: Color(0xff59646e),
            fontSize: 11,
            fontFamily: 'Roboto',
          ),
        ),
        textDirection: TextDirection.ltr,
      )..layout();
      final center = Offset(
        size.width / 2 + (number(label['x']) - b.center.dx) * scale,
        size.height / 2 - (number(label['y']) - b.center.dy) * scale,
      );
      text.paint(canvas, center - Offset(text.width / 2, text.height / 2));
    }
  }

  @override
  bool shouldRepaint(covariant MailerPainter oldDelegate) =>
      oldDelegate.preview != preview;
}
