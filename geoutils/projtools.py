"""
projtools provides a set of tools for dealing with different coordinate reference systems (CRS) and bounds.
"""
from __future__ import annotations

from collections import abc
from math import ceil, floor

import geopandas as gpd
import numpy as np
import pyproj
import rasterio as rio
import shapely.ops
from rasterio.crs import CRS
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import Polygon


def latlon_to_utm(lat: float, lon: float) -> str:
    """
    Get UTM zone for a given latitude and longitude coordinates.

    :param lat: Latitude coordinate.
    :param lon: Longitude coordinate.

    :returns: UTM zone.
    """

    if not (
        isinstance(lat, (float, np.floating, int, np.integer))
        and isinstance(lon, (float, np.floating, int, np.integer))
    ):
        raise TypeError("Latitude and longitude must be floats or integers.")

    if not -180 <= lon < 180:
        raise ValueError("Longitude value is out of range [-180, 180[.")
    if not -90 <= lat < 90:
        raise ValueError("Latitude value is out of range [-90, 90[.")

    # Get UTM zone from name string of crs info
    utm_zone = pyproj.database.query_utm_crs_info(
        "WGS 84", area_of_interest=pyproj.aoi.AreaOfInterest(lon, lat, lon, lat)
    )[0].name.split(" ")[-1]

    return str(utm_zone)


def utm_to_epsg(utm: str) -> int:
    """
    Get EPSG code of UTM zone.

    :param utm: UTM zone.

    :return: EPSG of UTM zone.
    """

    # Whether UTM is passed as single or double digits, homogenize to single-digit
    utm = str(int(utm[:-1])) + utm[-1].upper()

    # Get corresponding EPSG
    epsg = pyproj.CRS(f"WGS 84 / UTM Zone {utm}").to_epsg()

    return int(epsg)


def bounds2poly(
    boundsGeom: list[float] | rio.io.DatasetReader,
    in_crs: CRS | None = None,
    out_crs: CRS | None = None,
) -> Polygon:
    """
    Converts self's bounds into a shapely Polygon. Optionally, returns it into a different CRS.

    :param boundsGeom: A geometry with bounds. Can be either a list of coordinates (xmin, ymin, xmax, ymax),\
            a rasterio/Raster object, a geoPandas/Vector object
    :param in_crs: Input CRS
    :param out_crs: Output CRS

    :returns: Output polygon
    """
    # If boundsGeom is a GeoPandas or Vector object (warning, has both total_bounds and bounds attributes)
    if hasattr(boundsGeom, "total_bounds"):
        xmin, ymin, xmax, ymax = boundsGeom.total_bounds  # type: ignore
        in_crs = boundsGeom.crs  # type: ignore
    # If boundsGeom is a rasterio or Raster object
    elif hasattr(boundsGeom, "bounds"):
        xmin, ymin, xmax, ymax = boundsGeom.bounds  # type: ignore
        in_crs = boundsGeom.crs  # type: ignore
    # if a list of coordinates
    elif isinstance(boundsGeom, (list, tuple)):
        xmin, ymin, xmax, ymax = boundsGeom
    else:
        raise ValueError(
            "boundsGeom must a list/tuple of coordinates or an object with attributes bounds or total_bounds."
        )

    corners = ((xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax))

    if (in_crs is not None) & (out_crs is not None):
        corners = np.transpose(reproject_points(np.transpose(corners), in_crs, out_crs))

    bbox = Polygon(corners)

    return bbox


def merge_bounds(
    bounds_list: abc.Iterable[
        list[float] | tuple[float] | rio.coords.BoundingBox | rio.io.DatasetReader | gpd.GeoDataFrame
    ],
    resolution: float | None = None,
    merging_algorithm: str = "union",
    return_rio_bbox: bool = False,
) -> tuple[float, ...] | rio.coords.BoundingBox:
    """
    Merge a list of bounds into single bounds, using either the union or intersection.

    :param bounds_list: List of geometries with bounds, i.e. list of coordinates (xmin, ymin, xmax, ymax),
        rasterio bounds, a rasterio Dataset (or Raster), a geopandas object (or Vector).
    :param resolution: (For Rasters) Resolution, to make sure extent is a multiple of it.
    :param merging_algorithm: Algorithm to use for merging, either "union" or "intersection".
    :param return_rio_bbox: Whether to return a rio.coords.BoundingBox object instead of a tuple.

    :returns: Output bounds (xmin, ymin, xmax, ymax) or empty tuple
    """
    # Check that bounds_list is a list of bounds objects
    assert isinstance(bounds_list, (list, tuple)), "bounds_list must be a list/tuple"

    for bounds in bounds_list:
        assert hasattr(bounds, "bounds") or hasattr(bounds, "total_bounds") or isinstance(bounds, (list, tuple)), (
            "bounds_list must be a list of lists/tuples of coordinates or an object with attributes bounds "
            "or total_bounds"
        )

    output_poly = bounds2poly(boundsGeom=bounds_list[0])

    # Compute the merging
    for boundsGeom in bounds_list[1:]:
        new_poly = bounds2poly(boundsGeom)

        if merging_algorithm == "union":
            output_poly = output_poly.union(new_poly)
        elif merging_algorithm == "intersection":
            output_poly = output_poly.intersection(new_poly)
        else:
            raise ValueError("merging_algorithm must be 'union' or 'intersection'")

    # Get merged bounds, write as dict to manipulate with resolution in the next step
    new_bounds = output_poly.bounds
    rio_bounds = {"left": new_bounds[0], "bottom": new_bounds[1], "right": new_bounds[2], "top": new_bounds[3]}

    # Make sure that extent is a multiple of resolution
    if resolution is not None:
        for key1, key2 in zip(("left", "bottom"), ("right", "top")):
            modulo = (rio_bounds[key2] - rio_bounds[key1]) % resolution
            rio_bounds[key2] += modulo

    # Format output
    if return_rio_bbox:
        final_bounds = rio.coords.BoundingBox(**rio_bounds)
    else:
        final_bounds = tuple(rio_bounds.values())

    return final_bounds


def align_bounds(
    ref_transform: rio.transform.Affine,
    src_bounds: rio.coords.BoundingBox | tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """
    Aligns the bounds in src_bounds so that it matches the georeferences in ref_transform
    i.e. the distance between the upper-left pixels of ref and src is a multiple of resolution and
    the width/height of the bounds are a multiple of resolution.
    The bounds are padded so that the output bounds always contain the input bounds.

    :param ref_transform: The transform of the dataset to be used as reference
    :param src_bounds: The initial bounds that needs to be aligned to ref_transform. \
    Must be a rasterio BoundingBox or list or tuple with coordinates (left, bottom, right, top).

    :returns: the aligned bounding box (left, bottom, right, top)
    """
    left, bottom, right, top = src_bounds
    xres = ref_transform.a
    yres = ref_transform.e
    ref_left = ref_transform.xoff
    ref_top = ref_transform.yoff

    left = ref_left + floor((left - ref_left) / xres) * xres
    right = left + ceil((right - left) / xres) * xres
    top = ref_top + floor((top - ref_top) / yres) * yres
    bottom = top + ceil((bottom - top) / yres) * yres

    return (left, bottom, right, top)


def reproject_points(pts: list[list[float]] | np.ndarray, in_crs: CRS, out_crs: CRS) -> tuple[list[float], list[float]]:
    """
    Reproject a set of point from input_crs to output_crs.

    :param pts: Input points to be reprojected. Must be of shape (2, N), i.e (x coords, y coords)
    :param in_crs: Input CRS
    :param out_crs: Output CRS

    :returns: Reprojected points, of same shape as pts.
    """
    assert np.shape(pts)[0] == 2, "pts must be of shape (2, N)"

    x, y = pts
    transformer = pyproj.Transformer.from_crs(in_crs, out_crs)
    xout, yout = transformer.transform(x, y)
    return (xout, yout)


# Functions to convert from and to latlon

crs_4326 = rio.crs.CRS.from_epsg(4326)


def reproject_to_latlon(
    pts: list[list[float]] | np.ndarray, in_crs: CRS, round_: int = 8
) -> tuple[list[float], list[float]]:
    """
    Reproject a set of point from in_crs to lat/lon.

    :param pts: Input points to be reprojected. Must be of shape (2, N), i.e (x coords, y coords)
    :param in_crs: Input CRS
    :param round_: Output rounding. Default of 8 ensures cm accuracy

    :returns: Reprojected points, of same shape as pts.
    """
    proj_pts = reproject_points(pts, in_crs, crs_4326)
    proj_pts = np.round(proj_pts, round_)
    return proj_pts


def reproject_from_latlon(
    pts: list[list[float]] | tuple[list[float], list[float]] | np.ndarray, out_crs: CRS, round_: int = 2
) -> tuple[list[float], list[float]]:
    """
    Reproject a set of point from lat/lon to out_crs.

    :param pts: Input points to be reprojected. Must be of shape (2, N), i.e (x coords, y coords)
    :param out_crs: Output CRS
    :param round_: Output rounding. Default of 2 ensures cm accuracy

    :returns: Reprojected points, of same shape as pts.
    """
    proj_pts = reproject_points(pts, crs_4326, out_crs)
    proj_pts = np.round(proj_pts, round_)
    return proj_pts


def reproject_shape(inshape: BaseGeometry, in_crs: CRS, out_crs: CRS) -> BaseGeometry:
    """
    Reproject a shapely geometry from one CRS into another CRS.

    :param inshape: Shapely geometry to be reprojected.
    :param in_crs: Input CRS
    :param out_crs: Output CRS

    :returns: Reprojected geometry
    """
    reproj = pyproj.Transformer.from_crs(in_crs, out_crs, always_xy=True, skip_equivalent=True).transform
    return shapely.ops.transform(reproj, inshape)


def compare_proj(proj1: CRS, proj2: CRS) -> bool:
    """
    Compare two projections to see if they are the same, using pyproj.CRS.is_exact_same.

    :param proj1: The first projection to compare.
    :param proj2: The first projection to compare.

    :returns: True if the two projections are the same.
    """
    assert all(
        [isinstance(proj1, (pyproj.CRS, CRS)), isinstance(proj2, (pyproj.CRS, CRS))]
    ), "proj1 and proj2 must be rasterio.crs.CRS objects."
    proj1 = pyproj.CRS(proj1.to_string())
    proj2 = pyproj.CRS(proj2.to_string())

    same: bool = proj1.is_exact_same(proj2)
    return same


def _get_bounds_projected(
    bounds: rio.coords.BoundingBox, in_crs: CRS, out_crs: CRS, densify_pts: int = 5000
) -> rio.coords.BoundingBox:

    # Calculate new bounds
    left, bottom, right, top = bounds
    new_bounds = rio.warp.transform_bounds(in_crs, out_crs, left, bottom, right, top, densify_pts)
    new_bounds = rio.coords.BoundingBox(*new_bounds)

    return new_bounds
