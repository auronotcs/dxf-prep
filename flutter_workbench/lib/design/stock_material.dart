import 'package:flutter/material.dart';
import 'controller.dart';

const stockMaterials = [
  'Aluminum',
  'Acrylic',
  'Polycarbonate',
  'HDU',
  'Wood',
  'ACM',
  'Coroplast',
  'Anything',
];
const stockThicknesses = <String, double>{
  '.040 in': .040,
  '.063 in': .063,
  '.080 in': .080,
  '3 mm': 3 / 25.4,
  '.125 in': .125,
  '.150 in': .150,
  '4.5 mm': 4.5 / 25.4,
  '6 mm': 6 / 25.4,
  '.250 in': .250,
  '12 mm': 12 / 25.4,
  '.500 in': .500,
  '18 mm': 18 / 25.4,
  '.750 in (actual)': .750,
  '24 mm': 24 / 25.4,
  '1 in': 1,
  '2 in': 2,
  '3 in': 3,
};
double inchesToUnits(String units) => switch (units) {
  'mm' => 25.4,
  'cm' => 2.54,
  'm' => .0254,
  'ft' => 1 / 12,
  'yd' => 1 / 36,
  _ => 1,
};

/// Job-owned values shared by Design's stock bar and Manufacturing.
class StockMaterialControls extends StatelessWidget {
  const StockMaterialControls({super.key, required this.model});
  final DesignController model;

  Future<void> change(BuildContext context, String field, dynamic value) async {
    if (!await model.run('manufacturing.setup', {field: value}) &&
        context.mounted) {
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text(model.message)));
    }
  }

  Future<void> custom(
    BuildContext context,
    String field,
    dynamic current,
  ) async {
    final input = TextEditingController(text: current?.toString() ?? '');
    String? error;
    final value = await showDialog<dynamic>(
      context: context,
      builder: (context) => StatefulBuilder(
        builder: (context, update) => AlertDialog(
          title: Text(
            field == 'material'
                ? 'Custom material'
                : 'Stock thickness (${model.units})',
          ),
          content: TextField(
            controller: input,
            autofocus: true,
            decoration: InputDecoration(
              labelText: field == 'material'
                  ? 'Material name'
                  : 'Positive thickness',
              errorText: error,
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: () {
                final text = input.text.trim();
                final dynamic parsed = field == 'material'
                    ? text
                    : double.tryParse(text);
                if (text.isEmpty ||
                    (field != 'material' &&
                        (parsed == null || !parsed.isFinite || parsed <= 0))) {
                  update(
                    () => error =
                        'Enter ${field == 'material' ? 'a material name' : 'a positive finite thickness'}.',
                  );
                  return;
                }
                Navigator.pop(context, parsed);
              },
              child: const Text('Apply'),
            ),
          ],
        ),
      ),
    );
    if (value != null && context.mounted) await change(context, field, value);
    await Future<void>.delayed(const Duration(milliseconds: 250));
    input.dispose();
  }

  Future<void> verify(BuildContext context) async {
    final review = Map<String, dynamic>.from(
      model.state['manufacturing']?['stockVerification'] ?? {},
    );
    final setup = Map<String, dynamic>.from(model.state['setup'] ?? {});
    final checked = [false, false, false];
    bool applying = false;
    String? error;
    await showDialog<void>(
      context: context,
      barrierDismissible: false,
      builder: (dialogContext) => StatefulBuilder(
        builder: (context, update) => AlertDialog(
          title: const Text('Verify physical stock'),
          content: SizedBox(
            width: 520,
            child: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text(
                    'Check the actual sheet and machine setup. Changing material, thickness, breakthrough, machine or plates invalidates this confirmation.',
                  ),
                  const SizedBox(height: 12),
                  Text('Material: ${setup['material'] ?? 'Anything'}'),
                  Text(
                    'Thickness: ${setup['material_thickness'] ?? '-'} ${model.units}',
                  ),
                  Text(
                    'Breakthrough: ${setup['through_cut_breakthrough'] ?? .015 * inchesToUnits(model.units)} ${model.units}',
                  ),
                  Text(
                    'Machine: ${review['fingerprint']?['machine_profile'] ?? '-'}',
                  ),
                  for (final plate in review['plates'] as List? ?? [])
                    Text(
                      'Plate ${plate['number']}: ${plate['width']} x ${plate['height']} ${model.units}',
                    ),
                  if (review['eligible'] != true)
                    const Text(
                      'Set material, positive thickness and machine in Stock / job setup first.',
                    ),
                  for (final e in const [
                    'I checked the physical sheet material/label.',
                    'I checked or measured the physical sheet thickness.',
                    'I checked that sheet size and machine setup match this job.',
                  ].asMap().entries)
                    CheckboxListTile(
                      contentPadding: EdgeInsets.zero,
                      title: Text(e.value),
                      value: checked[e.key],
                      onChanged: applying
                          ? null
                          : (v) => update(() => checked[e.key] = v == true),
                    ),
                  if (error != null)
                    Text(
                      error!,
                      style: TextStyle(
                        color: Theme.of(context).colorScheme.error,
                      ),
                    ),
                ],
              ),
            ),
          ),
          actions: [
            TextButton(
              onPressed: applying ? null : () => Navigator.pop(dialogContext),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed:
                  applying ||
                      review['eligible'] != true ||
                      !checked.every((v) => v)
                  ? null
                  : () async {
                      update(() => applying = true);
                      final ok = await model.run('manufacturing.verify_stock', {
                        'token': review['token'],
                        'material_checked': checked[0],
                        'thickness_checked': checked[1],
                        'machine_size_checked': checked[2],
                      });
                      if (!dialogContext.mounted) return;
                      if (ok) {
                        Navigator.pop(dialogContext);
                      } else {
                        update(() {
                          applying = false;
                          error = model.message;
                        });
                      }
                    },
              child: const Text('Confirm stock'),
            ),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final setup = (model.state['setup'] as Json?) ?? {};
    final material = (setup['material'] as String?) ?? 'Anything';
    final thickness = setup['material_thickness'];
    final enabled =
        model.ready &&
        !model.busy &&
        model.state['manufacturing']?['editable'] == true;
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        const Text(
          'MATERIAL',
          style: TextStyle(fontSize: 10, fontWeight: FontWeight.w700),
        ),
        PopupMenuButton<String>(
          key: const ValueKey('stock-material'),
          enabled: enabled,
          tooltip: 'Stock material',
          onSelected: (v) => v == '__custom__'
              ? custom(context, 'material', material)
              : change(context, 'material', v),
          itemBuilder: (_) => [
            for (final name in {...stockMaterials, material})
              PopupMenuItem(value: name, child: Text(name)),
            const PopupMenuItem(
              value: '__custom__',
              child: Text('Custom material...'),
            ),
          ],
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 8),
            child: Row(
              children: [
                ConstrainedBox(
                  constraints: const BoxConstraints(maxWidth: 120),
                  child: Text(material, overflow: TextOverflow.ellipsis),
                ),
                const Icon(Icons.arrow_drop_down, size: 18),
              ],
            ),
          ),
        ),
        PopupMenuButton<String>(
          key: const ValueKey('stock-thickness'),
          enabled: enabled,
          tooltip: 'Stock thickness',
          onSelected: (v) => v == '__custom__'
              ? custom(context, 'material_thickness', thickness)
              : change(
                  context,
                  'material_thickness',
                  stockThicknesses[v]! * inchesToUnits(model.units),
                ),
          itemBuilder: (_) => [
            for (final e in stockThicknesses.entries)
              PopupMenuItem(value: e.key, child: Text(e.key)),
            PopupMenuItem(
              value: '__custom__',
              child: Text('Custom thickness (${model.units})...'),
            ),
          ],
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 8),
            child: Row(
              children: [
                Text(
                  '${thickness == null ? "Thickness" : (thickness as num).toStringAsFixed(3)} ${model.units}',
                ),
                const Icon(Icons.arrow_drop_down, size: 18),
              ],
            ),
          ),
        ),
        TextButton.icon(
          key: const ValueKey('stock-verification'),
          onPressed: enabled ? () => verify(context) : null,
          icon: Icon(
            model.state['manufacturing']?['stockVerification']?['verified'] ==
                    true
                ? Icons.verified_outlined
                : Icons.fact_check_outlined,
            size: 16,
          ),
          label: Text(
            model.state['manufacturing']?['stockVerification']?['verified'] ==
                    true
                ? 'Stock verified'
                : 'Verify stock',
          ),
        ),
      ],
    );
  }
}
