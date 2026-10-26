import 'package:flutter/material.dart';

/// Original Odessy pixel boat, matching installer/ui/brand.py; no icon font needed.
class PixelBoat extends StatelessWidget {
  const PixelBoat({super.key, this.size = 22});
  final double size;
  @override
  Widget build(BuildContext context) => Semantics(
      label: 'Odessy pixel boat',
      image: true,
      child: SizedBox.square(
          dimension: size, child: CustomPaint(painter: _BoatPainter())));
}

class _BoatPainter extends CustomPainter {
  static const rows = [
    '................',
    '.......M........',
    '.......MS.......',
    '.......MSS......',
    '.......MSSS.....',
    '.......MSSSS....',
    '.......MSSSSS...',
    '.......MSSSSSS..',
    '.......MSSSSS...',
    '.......MSSSS....',
    '.......MSSS.....',
    '.......MSS......',
    '.......M........',
    '..HHHHHHHHHHHH..',
    '...HHHHHHHHHH...',
    '.....HHHHHH.....'
  ];
  @override
  void paint(Canvas canvas, Size size) {
    final unit = size.shortestSide / 16;
    final paint = Paint()..isAntiAlias = false;
    for (var y = 0; y < rows.length; y++) {
      for (var x = 0; x < 16; x++) {
        final p = rows[y][x];
        if (p == '.') continue;
        paint.color =
            p == 'S' ? const Color(0xff5b8def) : const Color(0xff8fb8dc);
        canvas.drawRect(Rect.fromLTWH(x * unit, y * unit, unit, unit), paint);
      }
    }
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
}
