#include <algorithm>
#include <array>
#include <bit>
#include <cstdint>
#include <iostream>
#include <limits>
#include <vector>

using u32 = std::uint32_t;
using u64 = std::uint64_t;
constexpr int P = 101;
constexpr int LABELS = 625;
constexpr int EDGE_VALUES = 65;
constexpr int RAW_BINS = P * P * EDGE_VALUES;
constexpr int PHASE_BINS = P * P * P;
constexpr u64 FNV_OFFSET = 1469598103934665603ULL;

int power(int base, int exponent) {
    int value = 1;
    while (exponent) {
        if (exponent & 1) value = value * base % P;
        base = base * base % P;
        exponent >>= 1;
    }
    return value;
}

void mix(u64& hash, u64 value) {
    for (int byte = 0; byte < 8; ++byte) {
        hash ^= (value >> (8 * byte)) & 255;
        hash *= 1099511628211ULL;
    }
}

struct Shape {
    std::uint16_t label;
    std::array<std::uint8_t, 4> row6;
    std::array<std::uint8_t, 4> row8;
};

struct Digest {
    u64 sum = 0;
    u64 xors = 0;
    u64 squares = 0;
};

struct Result {
    u64 pair_visits = 0;
    u64 raw_mass = 0;
    u64 phase_mass = 0;
    u64 raw_support = 0;
    u64 phase_support = 0;
    u64 raw_maximum = 0;
    u64 phase_maximum = 0;
    u64 phase_merges = 0;
    u64 transport_checks = 0;
    u64 transport_failures = 0;
    Digest raw_digest;
    Digest phase_digest;
    u64 raw_boundary_checksum = FNV_OFFSET;
    u64 phase_boundary_checksum = FNV_OFFSET;
    int raw_boundary_minimum = RAW_BINS;
    int raw_boundary_maximum = 0;
    int phase_boundary_minimum = PHASE_BINS;
    int phase_boundary_maximum = 0;
    std::array<u64, 66> raw_histogram{};
    std::array<u64, 102> phase_histogram{};
    std::array<u64, P> raw_value_mass{};
    std::array<u64, P> phase_value_mass{};
};

u64 hashed_record(int label, u32 key, u32 coefficient) {
    u64 hash = FNV_OFFSET;
    mix(hash, label);
    mix(hash, key);
    mix(hash, coefficient);
    return hash;
}

void accumulate_digest(Digest& digest, int label, u32 key, u32 coefficient) {
    u64 hash = hashed_record(label, key, coefficient);
    digest.sum += hash;
    digest.xors ^= std::rotl(hash, (label + key) % 64);
    digest.squares += hash * hash;
}

Result run_side(
    bool left,
    const std::vector<Shape>& shapes,
    const std::vector<u32>& zero_shapes,
    const std::array<std::vector<u32>, LABELS>& labeled_zero_shapes,
    const std::array<std::uint8_t, LABELS * LABELS>& edge,
    const std::array<int, P>& edge_index,
    const std::array<int, EDGE_VALUES>& edge_values,
    const std::array<int, 5>& phases
) {
    Result result;
    std::vector<u32> raw_counts(RAW_BINS);
    std::vector<u32> closed_counts(PHASE_BINS);
    std::vector<u32> raw_touched;
    std::vector<u32> closed_touched;
    raw_touched.reserve(RAW_BINS);
    closed_touched.reserve(PHASE_BINS);
    std::array<std::uint8_t, P * P> raw_lengths{};
    std::array<std::uint8_t, P * P> closed_lengths{};
    std::vector<u32> raw_bases;
    std::vector<u32> closed_bases;
    raw_bases.reserve(P * P);
    closed_bases.reserve(P * P);
    int low_residue = left ? 0 : 2;
    int high_residue = left ? 1 : 3;
    int beta = left ? 1 : 87;

    for (int boundary = 0; boundary < LABELS; ++boundary) {
        const auto& fixed = labeled_zero_shapes[boundary];
        for (u32 outside_code : zero_shapes) {
            const Shape& outside = shapes[outside_code];
            for (u32 boundary_code : fixed) {
                const Shape& inside = shapes[boundary_code];
                const Shape& first = left ? outside : inside;
                const Shape& second = left ? inside : outside;
                int unary6 = (first.row6[low_residue] + second.row6[high_residue]) % P;
                int unary8 = (first.row8[low_residue] + second.row8[high_residue]) % P;
                int edge_value = left ? edge[outside.label * LABELS + boundary]
                                      : edge[boundary * LABELS + outside.label];
                int base = unary6 * P + unary8;
                int key = base * EDGE_VALUES + edge_index[edge_value];
                if (raw_counts[key] == 0) {
                    raw_touched.push_back(key);
                    if (raw_lengths[base] == 0) raw_bases.push_back(base);
                    ++raw_lengths[base];
                }
                if (raw_counts[key] == std::numeric_limits<u32>::max()) return Result{};
                ++raw_counts[key];
                ++result.pair_visits;
            }
        }

        u64 raw_boundary = raw_touched.size();
        mix(result.raw_boundary_checksum, boundary);
        mix(result.raw_boundary_checksum, raw_boundary);
        result.raw_boundary_minimum = std::min(result.raw_boundary_minimum, int(raw_boundary));
        result.raw_boundary_maximum = std::max(result.raw_boundary_maximum, int(raw_boundary));
        result.raw_support += raw_boundary;
        result.raw_histogram[0] += P * P - raw_bases.size();
        for (u32 base : raw_bases) {
            ++result.raw_histogram[raw_lengths[base]];
            raw_lengths[base] = 0;
        }
        raw_bases.clear();

        for (u32 key : raw_touched) {
            u32 coefficient = raw_counts[key];
            int base = key / EDGE_VALUES;
            int edge_value = edge_values[key % EDGE_VALUES];
            result.raw_mass += coefficient;
            result.raw_value_mass[edge_value] += coefficient;
            result.raw_maximum = std::max(result.raw_maximum, u64(coefficient));
            accumulate_digest(result.raw_digest, boundary, key, coefficient);
            int unary6 = base / P;
            int unary8 = base % P;
            for (int exponent = 1; exponent < 5; ++exponent) {
                int moved6 = unary6 * phases[exponent] % P;
                int moved8 = unary8 * power(phases[exponent], P - 2) % P;
                int moved_key = (moved6 * P + moved8) * EDGE_VALUES + key % EDGE_VALUES;
                ++result.transport_checks;
                if (raw_counts[moved_key] != coefficient) ++result.transport_failures;
            }
            for (int exponent = 4; exponent >= 0; --exponent) {
                int z = beta * phases[exponent] % P * edge_value % P;
                int closed_key = base * P + z;
                if (closed_counts[closed_key] == 0) {
                    closed_touched.push_back(closed_key);
                    if (closed_lengths[base] == 0) closed_bases.push_back(base);
                    ++closed_lengths[base];
                } else {
                    ++result.phase_merges;
                }
                if (u64(closed_counts[closed_key]) + coefficient > std::numeric_limits<u32>::max()) return Result{};
                closed_counts[closed_key] += coefficient;
            }
        }

        u64 phase_boundary = closed_touched.size();
        mix(result.phase_boundary_checksum, boundary);
        mix(result.phase_boundary_checksum, phase_boundary);
        result.phase_boundary_minimum = std::min(result.phase_boundary_minimum, int(phase_boundary));
        result.phase_boundary_maximum = std::max(result.phase_boundary_maximum, int(phase_boundary));
        result.phase_support += phase_boundary;
        result.phase_histogram[0] += P * P - closed_bases.size();
        for (u32 base : closed_bases) {
            ++result.phase_histogram[closed_lengths[base]];
            closed_lengths[base] = 0;
        }
        closed_bases.clear();

        for (u32 key : closed_touched) {
            u32 coefficient = closed_counts[key];
            int z = key % P;
            result.phase_mass += coefficient;
            result.phase_value_mass[z] += coefficient;
            result.phase_maximum = std::max(result.phase_maximum, u64(coefficient));
            accumulate_digest(result.phase_digest, boundary, key, coefficient);
            closed_counts[key] = 0;
        }
        closed_touched.clear();
        for (u32 key : raw_touched) raw_counts[key] = 0;
        raw_touched.clear();
    }
    return result;
}

template <std::size_t Size>
void print_array(const std::array<u64, Size>& values) {
    for (std::size_t index = 0; index < Size; ++index) {
        if (index) std::cout << ',';
        std::cout << values[index];
    }
    std::cout << '\n';
}

void print_pair(u64 first, u64 second) { std::cout << first << ',' << second << '\n'; }

void print_digests(const Digest& first, const Digest& second) {
    std::cout << first.sum << ',' << first.xors << ',' << first.squares << ','
              << second.sum << ',' << second.xors << ',' << second.squares << '\n';
}

int main() {
    std::array<int, 25> roots{};
    std::array<int, 25> inverse_roots{};
    for (int exponent = 0; exponent < 25; ++exponent) {
        roots[exponent] = power(5, exponent);
        inverse_roots[exponent] = power(roots[exponent], 24);
    }
    std::array<int, 5> phases{};
    for (int exponent = 0; exponent < 5; ++exponent) phases[exponent] = power(95, exponent);
    std::array<std::array<int, 25>, 2> coefficient_table{};
    for (int coordinate = 0; coordinate < 25; ++coordinate) {
        coefficient_table[0][coordinate] = power(5, ((-3 * coordinate) % 25 + 25) % 25);
        coefficient_table[1][coordinate] = power(5, ((-6 * coordinate) % 25 + 25) % 25);
    }

    std::array<std::array<std::uint8_t, 5>, LABELS> label_values{};
    for (int label = 0; label < LABELS; ++label) {
        int work = label;
        label_values[label][0] = 1;
        for (int quotient = 1; quotient < 5; ++quotient) {
            label_values[label][quotient] = phases[work % 5];
            work /= 5;
        }
    }
    std::array<std::uint8_t, LABELS * LABELS> edge{};
    std::array<bool, P> edge_seen{};
    u64 edge_checksum = FNV_OFFSET;
    for (int first = 0; first < LABELS; ++first) {
        for (int second = 0; second < LABELS; ++second) {
            int value = 0;
            for (int quotient = 0; quotient < 5; ++quotient) {
                value = (value + power(label_values[first][quotient], P - 2) * label_values[second][quotient]) % P;
            }
            edge[first * LABELS + second] = value;
            edge_seen[value] = true;
            mix(edge_checksum, first);
            mix(edge_checksum, second);
            mix(edge_checksum, value);
        }
    }
    std::array<int, P> edge_index{};
    std::array<int, EDGE_VALUES> edge_values{};
    edge_index.fill(-1);
    int edge_count = 0;
    for (int value = 0; value < P; ++value) {
        if (edge_seen[value]) {
            edge_index[value] = edge_count;
            edge_values[edge_count++] = value;
        }
    }
    if (edge_count != EDGE_VALUES) return 71;
    std::array<int, P> phase_preimages{};
    for (int value = 0; value < P; ++value) if (edge_seen[value]) {
        for (int exponent = 0; exponent < 5; ++exponent) ++phase_preimages[value * phases[exponent] % P];
    }
    int multiple_preimages = 0;
    int maximum_preimages = 0;
    for (int value : phase_preimages) {
        if (value > 1) ++multiple_preimages;
        maximum_preimages = std::max(maximum_preimages, value);
    }

    std::vector<Shape> shapes(390625);
    std::vector<u32> zero_shapes;
    std::array<std::vector<u32>, LABELS> labeled_zero_shapes;
    std::array<u64, LABELS> zero_label_counts{};
    for (u32 code = 0; code < shapes.size(); ++code) {
        u32 work = code;
        std::array<int, 5> exponents{};
        std::array<int, 5> values{{1}};
        std::array<int, 5> inverses{{1}};
        int label = 0;
        int label_place = 1;
        for (int quotient = 1; quotient < 5; ++quotient) {
            exponents[quotient] = work % 25;
            work /= 25;
            values[quotient] = roots[exponents[quotient]];
            inverses[quotient] = inverse_roots[exponents[quotient]];
            label += (exponents[quotient] % 5) * label_place;
            label_place *= 5;
        }
        int bucket = 0;
        for (int quotient = 0; quotient < 5; ++quotient) bucket = (bucket + inverses[quotient] * values[(quotient + 1) % 5]) % P;
        Shape shape{};
        shape.label = label;
        for (int residue = 0; residue < 4; ++residue) {
            int row6 = 0;
            int row8 = 0;
            for (int quotient = 0; quotient < 5; ++quotient) {
                int coordinate = residue + 5 * quotient;
                row6 = (row6 + coefficient_table[0][coordinate] * inverses[quotient] % P * values[(quotient + 1) % 5]) % P;
                row8 = (row8 + coefficient_table[1][coordinate] * inverses[quotient] % P * values[(quotient + 4) % 5]) % P;
            }
            shape.row6[residue] = row6;
            shape.row8[residue] = row8;
        }
        shapes[code] = shape;
        if (bucket == 0) {
            zero_shapes.push_back(code);
            labeled_zero_shapes[label].push_back(code);
            ++zero_label_counts[label];
        }
    }

    Result left = run_side(true, shapes, zero_shapes, labeled_zero_shapes, edge, edge_index, edge_values, phases);
    Result right = run_side(false, shapes, zero_shapes, labeled_zero_shapes, edge, edge_index, edge_values, phases);
    if (!left.raw_mass || !right.raw_mass) return 72;
    u64 shape_bytes = u64(shapes.size()) * sizeof(Shape);
    u64 zero_code_bytes = u64(zero_shapes.size()) * 2 * sizeof(u32);
    u64 dense_payload_bytes = u64(RAW_BINS + PHASE_BINS) * 2 * sizeof(u32);

    std::cout << "REASONING_AUGMENTED_FOUR_BLOCK_ROW0_FIXED_FIBER_PAIR_CALIBRATION_V1\n";
    std::cout << "field_prime=" << P << "\nomega=95\nlabel_count=" << LABELS << "\nedge_value_count=" << edge_count << "\nedge_checksum=" << edge_checksum << '\n';
    std::cout << "edge_phase_multiple_preimage_value_count=" << multiple_preimages << "\nedge_phase_maximum_preimage_count=" << maximum_preimages << '\n';
    std::cout << "shape_count=" << shapes.size() << "\nzero_bucket_population=" << zero_shapes.size() << "\nzero_bucket_label_support=" << std::count_if(zero_label_counts.begin(), zero_label_counts.end(), [](u64 value) { return value != 0; }) << '\n';
    std::cout << "beta_values=1,84,87\nblock_scale_coefficients=1,1,1,1\n";
    std::cout << "pair_visits="; print_pair(left.pair_visits, right.pair_visits);
    std::cout << "raw_masses="; print_pair(left.raw_mass, right.raw_mass);
    std::cout << "phase_masses="; print_pair(left.phase_mass, right.phase_mass);
    std::cout << "raw_support_counts="; print_pair(left.raw_support, right.raw_support);
    std::cout << "phase_support_counts="; print_pair(left.phase_support, right.phase_support);
    std::cout << "raw_maximum_coefficients="; print_pair(left.raw_maximum, right.raw_maximum);
    std::cout << "phase_maximum_coefficients="; print_pair(left.phase_maximum, right.phase_maximum);
    std::cout << "phase_collision_merge_counts="; print_pair(left.phase_merges, right.phase_merges);
    std::cout << "raw_record_digests="; print_digests(left.raw_digest, right.raw_digest);
    std::cout << "phase_record_digests="; print_digests(left.phase_digest, right.phase_digest);
    std::cout << "raw_boundary_support_checksums="; print_pair(left.raw_boundary_checksum, right.raw_boundary_checksum);
    std::cout << "phase_boundary_support_checksums="; print_pair(left.phase_boundary_checksum, right.phase_boundary_checksum);
    std::cout << "raw_boundary_support_minima="; print_pair(left.raw_boundary_minimum, right.raw_boundary_minimum);
    std::cout << "raw_boundary_support_maxima="; print_pair(left.raw_boundary_maximum, right.raw_boundary_maximum);
    std::cout << "phase_boundary_support_minima="; print_pair(left.phase_boundary_minimum, right.phase_boundary_minimum);
    std::cout << "phase_boundary_support_maxima="; print_pair(left.phase_boundary_maximum, right.phase_boundary_maximum);
    std::cout << "raw_list_length_histogram_left="; print_array(left.raw_histogram);
    std::cout << "raw_list_length_histogram_right="; print_array(right.raw_histogram);
    std::cout << "phase_list_length_histogram_left="; print_array(left.phase_histogram);
    std::cout << "phase_list_length_histogram_right="; print_array(right.phase_histogram);
    std::cout << "raw_edge_value_masses_left="; print_array(left.raw_value_mass);
    std::cout << "raw_edge_value_masses_right="; print_array(right.raw_value_mass);
    std::cout << "phase_value_masses_left="; print_array(left.phase_value_mass);
    std::cout << "phase_value_masses_right="; print_array(right.phase_value_mass);
    std::cout << "raw_transport_check_counts="; print_pair(left.transport_checks, right.transport_checks);
    std::cout << "raw_transport_failure_counts="; print_pair(left.transport_failures, right.transport_failures);
    std::cout << "shape_payload_bytes=" << shape_bytes << "\nzero_code_payload_bytes=" << zero_code_bytes << "\ndense_counter_and_touched_payload_bytes=" << dense_payload_bytes << "\nREASONING_END\n";
}
