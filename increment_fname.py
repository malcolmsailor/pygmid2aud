import math
import os


def increment_fname(
    path, n_digits=3, overwrite=False, allow_increase_n_digits=True
):
    def _sub(path, n_digits):
        def _get_int_at_end_of_string(string):
            i = 0
            while True:
                try:
                    int(string[-(i + 1) :])
                except ValueError:
                    if i == 0:
                        return None, string, n_digits
                    return int(string[-i:]), string[:-i], i
                i += 1

        root, ext = os.path.splitext(path)
        count, base_str, n_digits = _get_int_at_end_of_string(root)
        if count is None or count < 0:
            count = 0
        elif math.log10(count + 1) >= n_digits:
            if allow_increase_n_digits:
                n_digits += 1
            else:
                raise NotImplementedError("Too many digits to increment")
        i_str = str(count + 1).zfill(n_digits)
        return "".join([base_str, i_str, ext])

    while True:
        path = _sub(path, n_digits)
        if overwrite or not os.path.exists(path):
            return path
